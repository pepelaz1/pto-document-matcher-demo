"""Небольшой локальный прототип: текстовые PDF, разделение и сборка комплекта."""
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import pymupdf
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent
INVALID = re.compile(r'[\\/:*?"<>|\r\n]+')
DOC_START = re.compile(r'^(?:СЕРТИФИКАТ|ПАСПОРТ|ДЕКЛАРАЦИЯ|ОТКАЗНОЕ ПИСЬМО)\b', re.I)
APPENDIX = re.compile(r'^ПРИЛОЖЕНИЕ\s+К\s+(?:СЕРТИФИКАТУ|ДЕКЛАРАЦИИ|ПАСПОРТУ)', re.I)


def safe_name(value, limit=70):
    value = re.sub(r'\s+', ' ', INVALID.sub(' ', value)).strip(' ._')
    return value[:limit].rstrip(' ._') or 'Без названия'


def normalize_text(value):
    return value.replace('\u00a0', ' ').replace('\u00ad', '-')


def stable_file(path):
    if path.suffix.lower() in {'.tmp', '.part'}:
        return False
    first = path.stat().st_size
    if first == 0:
        return False
    # В прототипе проверка мгновенная; в рабочей версии размер проверяется повторно через 3 с.
    return True


def ocr_page(page):
    exe = Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')
    if not exe.exists():
        raise RuntimeError('Для скана нужен Tesseract с языком rus; сейчас доступен только текстовый PDF.')
    langs = subprocess.run([str(exe), '--list-langs'], capture_output=True, text=True).stdout
    if 'rus' not in langs.split():
        raise RuntimeError('Установите языковой пакет rus для Tesseract, чтобы читать русские сканы.')
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / 'page.png'
        img.write_bytes(page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes('png'))
        result = subprocess.run([str(exe), str(img), 'stdout', '-l', 'rus+eng', '--psm', '6'],
                                capture_output=True, text=True, check=True)
        return result.stdout


def pages_with_cache(path, db):
    digest = hashlib.md5(path.read_bytes()).hexdigest()
    row = db.execute('SELECT pages FROM cache WHERE md5=?', (digest,)).fetchone()
    if row:
        return [normalize_text(x) for x in json.loads(row[0])]
    with pymupdf.open(path) as pdf:
        pages = []
        for page in pdf:
            text = normalize_text(page.get_text('text'))
            if len(text.strip()) < 30:
                text = ocr_page(page)
            pages.append(text)
    db.execute('INSERT OR REPLACE INTO cache VALUES (?, ?, ?)',
               (digest, str(path), json.dumps(pages, ensure_ascii=False)))
    db.commit()
    return pages


def parse_spec(pages):
    rows, notes = [], []
    in_notes = False
    for line in '\n'.join(pages).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith('примечания'):
            in_notes = True
            continue
        if in_notes:
            notes.append(line)
            continue
        cells = [x.strip() for x in line.split('|')]
        if len(cells) < 2:
            continue
        if re.fullmatch(r'\d+[а-яa-z]?', cells[0], re.I):
            rows.append({'position': cells[0], 'name': cells[1],
                         'quantity': cells[2] if len(cells) > 2 else ''})
        elif not cells[0] and rows and cells[1]:
            rows[-1]['name'] += ' ' + cells[1]
    return rows, ' '.join(notes)


def split_documents(path, pages):
    groups = []
    for index, text in enumerate(pages):
        first = next((x.strip() for x in text.splitlines() if x.strip()), '')
        if DOC_START.match(first) and not APPENDIX.match(first):
            groups.append([index])
        elif groups:
            groups[-1].append(index)
    return [{'source': path, 'pages': group, 'text': '\n'.join(pages[i] for i in group)}
            for group in groups]


def metadata(doc):
    text = doc['text']
    first = next((x.strip() for x in text.splitlines() if x.strip()), '')
    number = re.search(r'№\s*([\w\-/]+)', first)
    issued = re.search(r'Дата выдачи\s*:\s*(\d{2}\.\d{2}\.\d{4})', text, re.I)
    expires = re.search(r'Действителен до\s*:\s*(\d{2}\.\d{2}\.\d{4})', text, re.I)
    return {**doc, 'type': first.split(' №')[0], 'number': number.group(1) if number else 'бн',
            'issued': issued.group(1) if issued else '',
            'expires': expires.group(1) if expires else ''}


def score(row, doc):
    a, b = row['name'].lower(), doc['text'].lower()
    gost_a = set(re.findall(r'гост\s*[\d-]+', a))
    gost_b = set(re.findall(r'гост\s*[\d-]+', b))
    diam_a = set(re.findall(r'(?:ду|dn)\s*\d+', a))
    diam_b = set(re.findall(r'(?:ду|dn)\s*\d+', b))
    if gost_a and gost_b and not (gost_a & gost_b):
        return 0.0
    if diam_a and diam_b and not (diam_a & diam_b):
        return 0.0
    words_a = set(re.findall(r'[а-яёa-z]{4,}', a)) - {'гост'}
    words_b = set(re.findall(r'[а-яёa-z]{4,}', b))
    overlap = len(words_a & words_b) / max(1, len(words_a))
    exact_words = len(words_a) >= 2 and words_a <= words_b
    return round(min(1.0, 0.2 + 0.45 * overlap + 0.2 * bool(gost_a & gost_b)
                     + 0.15 * bool(diam_a & diam_b) + 0.2 * exact_words), 2)


def unique_target(folder, name, directory=False):
    target = folder / name
    counter = 2
    while target.exists():
        target = folder / (f'{name}_{counter}' if directory else f'{Path(name).stem}_{counter}{Path(name).suffix}')
        counter += 1
    return target


def build(spec_path, archive, output_root, code='', delivery_date=''):
    archive, output_root = Path(archive), Path(output_root)
    if not archive.is_dir():
        raise ValueError(f'Папка архива недоступна: {archive}')
    output_root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ROOT / 'cache.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS cache(md5 TEXT PRIMARY KEY, path TEXT, pages TEXT)')
    try:
        spec_pages = pages_with_cache(spec_path, db)
        rows, notes = parse_spec(spec_pages)
        if not rows:
            raise ValueError('Не найдены строки таблицы. Прототип ожидает PDF с колонками через |.')
        documents = []
        for pdf in sorted(archive.rglob('*.pdf')):
            if stable_file(pdf):
                documents.extend(metadata(d) for d in split_documents(pdf, pages_with_cache(pdf, db)))
        code = safe_name(code or spec_path.stem.split('_')[0], 40)
        folder = unique_target(output_root, f'{code}_{safe_name(spec_path.stem, 50)}_{datetime.now():%d.%m.%Y}', directory=True)
        folder.mkdir()
        shutil.copy2(spec_path, folder / spec_path.name)
        review_dir = folder / '_На_проверку'
        review_dir.mkdir()
        exclusions = [x.strip().lower() for x in (ROOT / 'exceptions.txt').read_text(encoding='utf-8').splitlines() if x.strip()]
        register, deficits = [], []
        for row in rows:
            if any(word in row['name'].lower() for word in exclusions):
                row.update(status='Не требует паспорта', matches=[])
                continue
            candidates = sorted(((score(row, doc), doc) for doc in documents), key=lambda x: x[0], reverse=True)
            candidates = [(value, doc) for value, doc in candidates if value >= 0.55]
            if not candidates:
                row.update(status='Не найдено', matches=[])
                deficits.append(row)
                continue
            row['matches'] = []
            row['status'] = 'Найдено'
            for value, doc in candidates:
                expired = bool(delivery_date and doc['expires'] and
                               datetime.strptime(doc['expires'], '%d.%m.%Y') < datetime.strptime(delivery_date, '%d.%m.%Y'))
                review = value < 0.8 or expired
                if review:
                    row['status'] = 'На проверку'
                target_dir = review_dir if review else folder
                filename = safe_name(f"{code}_{safe_name(row['name'])}_№{doc['number']}_{doc['issued']}", 180) + '.pdf'
                target = unique_target(target_dir, filename)
                with pymupdf.open(doc['source']) as source:
                    with pymupdf.open() as result:
                        for page_no in doc['pages']:
                            result.insert_pdf(source, from_page=page_no, to_page=page_no)
                        result.save(target)
                row['matches'].append({'file': str(target), 'file_name': target.name,
                                       'score': value, 'expired': expired})
                register.append([len(register)+1, row['position'], row['name'], target.name,
                                 f"{doc['type']} №{doc['number']}", doc['issued'], doc['expires'],
                                 'На проверку' if review else 'Найдено'])
        wb = Workbook()
        sheet = wb.active
        sheet.title = 'Реестр документов'
        sheet.append(['№ п/п', 'Позиция', 'Наименование', 'Имя итогового файла',
                      'Тип и номер', 'Дата выдачи', 'Срок действия', 'Статус'])
        for item in register:
            sheet.append(item)
        sheet = wb.create_sheet('Дефицит (Не найдено)')
        sheet.append(['Позиция', 'Наименование', 'Количество'])
        for item in deficits:
            sheet.append([item['position'], item['name'], item['quantity']])
        wb.save(folder / 'Реестр_ИД.xlsx')
        return {'folder': str(folder), 'folder_name': folder.name, 'rows': rows, 'notes': notes,
                'documents_indexed': len(documents), 'registry': str(folder / 'Реестр_ИД.xlsx')}
    finally:
        db.close()
