"""Создаёт небольшие искусственные PDF для проверки прототипа."""
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parent / 'sample'
FONT = r'C:\Windows\Fonts\arial.ttf'


def write_pdf(path, pages):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as pdf:
        for lines in pages:
            page = pdf.new_page()
            page.insert_font(fontname='ArialCyr', fontfile=FONT)
            y = 70
            for line in lines:
                page.insert_text((55, y), line, fontname='ArialCyr', fontsize=12)
                y += 26
        pdf.save(path)


def main():
    write_pdf(ROOT / 'specification_142-26-OV.pdf', [[
        'Спецификация 142-26-ОВ',
        'Поз. | Наименование и техническая характеристика | Кол.',
        '1 | Труба ВГП Ду 25 ГОСТ 3262-75 | 12',
        '  | водогазопроводная стальная |',
        '2 | Клапан огнезадерживающий КОМ-1 | 2',
        '3 | Болт М8 | 20',
        '4 | Насос циркуляционный НЦ-10 | 1',
        'Примечания:',
        'Клапаны поставляются с фланцами.',
    ]])
    write_pdf(ROOT / '01_Входящие_паспорта' / 'mixed_scan_example.pdf', [
        ['СЕРТИФИКАТ № C-101', 'Дата выдачи: 18.05.2026',
         'Действителен до: 18.05.2027', 'Труба ВГП Ду 25 ГОСТ 3262-75',
         'Труба водогазопроводная стальная'],
        ['ПРИЛОЖЕНИЕ К СЕРТИФИКАТУ № C-101',
         'Перечень продукции:', 'Труба ВГП Ду 25 ГОСТ 3262-75',
         'Труба ВГП Ду 32 ГОСТ 3262-75'],
        ['ПАСПОРТ № 884-П', 'Дата выдачи: 12.04.2026',
         'Клапан огнезадерживающий КОМ-1'],
    ])
    print(f'Тестовые документы созданы: {ROOT}')


if __name__ == '__main__':
    main()
