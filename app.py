"""Простой локальный веб-интерфейс для прототипа ПТО."""
import argparse
import base64
import json
import os
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from engine import build
from make_sample import ROOT as SAMPLE, main as make_sample

ROOT = Path(__file__).resolve().parent
HTML = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ПТО · Подбор документов</title><style>
:root{font:16px Arial,sans-serif;color:#172333;background:#f5f7fa}*{box-sizing:border-box}body{margin:0}main{max-width:1050px;margin:34px auto;padding:0 22px}h1{font-size:32px;margin:0 0 8px}p{color:#536579;line-height:1.5}.card{background:#fff;border:1px solid #dce3ea;border-radius:14px;padding:22px;margin:18px 0}label{display:block;font-weight:600;margin:14px 0 6px}input{width:100%;padding:11px 12px;border:1px solid #b9c6d3;border-radius:7px;font:inherit}button{background:#1762d2;color:white;border:0;border-radius:8px;padding:12px 18px;font:600 16px Arial;cursor:pointer}button:disabled{opacity:.55}table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid #e4e9ef;padding:11px;vertical-align:top}th{color:#536579;font-size:14px}.muted{color:#66788d}.good{color:#117344}.review{color:#a56a00}.missing{color:#bd3544}.skip{color:#697789}code{font-size:14px;word-break:break-all}#error{color:#bd3544}
</style><main><h1>Подбор паспортов ПТО</h1><p>Локальный прототип. Демонстрационные PDF созданы специально для проверки алгоритма. Внешние OCR и LLM пока не подключены.</p>
<section class="card"><strong>Входные данные</strong><p>По умолчанию используются два локальных тестовых PDF: спецификация и сборный файл с сертификатом, приложением и паспортом.</p>
<label>Спецификация PDF (необязательно)</label><input type="file" id="file" accept=".pdf">
<label>Шифр проекта</label><input id="code" value="142-26-ОВ">
<label>Дата поставки / АОСР</label><input id="date" placeholder="ДД.ММ.ГГГГ" value="20.06.2026">
<label>Папка с паспортами на этом компьютере</label><input id="archive" placeholder="Оставьте пустым для тестового пакета">
<p><button id="run">Подобрать паспорта</button></p><p class="muted">Исходные паспорта не изменяются. Результат создаётся в отдельной папке.</p></section>
<section class="card" id="result" hidden><strong>Результат</strong><p id="summary"></p><p id="notes"></p><div style="overflow:auto"><table><thead><tr><th>Поз.</th><th>Строка спецификации</th><th>Статус</th><th>Файл</th></tr></thead><tbody id="rows"></tbody></table></div><p>Комплект: <code id="folder"></code></p></section><p id="error"></p>
</main><script>
const el=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
el('run').onclick=async()=>{el('run').disabled=true;el('error').textContent='';el('result').hidden=true;try{const file=el('file').files[0];let content=null;if(file){content=await new Promise((ok,no)=>{const r=new FileReader();r.onload=()=>ok(String(r.result).split(',')[1]);r.onerror=no;r.readAsDataURL(file)})}const response=await fetch('/api/run',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({file_name:file?.name,content,code:el('code').value,date:el('date').value,archive:el('archive').value})});const data=await response.json();if(!response.ok)throw Error(data.error);el('summary').textContent=`Строк: ${data.rows.length} · документов в архиве: ${data.documents_indexed}`;el('notes').textContent=data.notes?'Примечания: '+data.notes:'';el('folder').textContent='output/'+data.folder_name;el('rows').innerHTML=data.rows.map(r=>`<tr><td>${esc(r.position)}</td><td>${esc(r.name)}</td><td>${esc(r.status)}</td><td>${esc(r.matches.map(m=>m.file_name).join('; '))}</td></tr>`).join('');el('result').hidden=false}catch(e){el('error').textContent=e.message}finally{el('run').disabled=false}};
</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path != '/':
            self.send_error(404)
            return
        body = HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/api/run':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 25_000_000:
                raise ValueError('Файл слишком большой для демонстрации (максимум 18 МБ).')
            payload = json.loads(self.rfile.read(length))
            archive = Path(payload.get('archive') or SAMPLE / '01_Входящие_паспорта')
            temp = None
            if payload.get('content'):
                name = Path(payload.get('file_name') or 'specification.pdf').name
                if not name.lower().endswith('.pdf'):
                    raise ValueError('Загрузите PDF-спецификацию.')
                temp = tempfile.TemporaryDirectory()
                spec = Path(temp.name) / name
                spec.write_bytes(base64.b64decode(payload['content'], validate=True))
            else:
                spec = SAMPLE / 'specification_142-26-OV.pdf'
            try:
                result = build(spec, archive, ROOT / 'output', payload.get('code', ''), payload.get('date', ''))
            finally:
                if temp:
                    temp.cleanup()
            self.respond(200, result)
        except Exception as exc:
            self.respond(400, {'error': str(exc)})

    def respond(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8501)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not (SAMPLE / 'specification_142-26-OV.pdf').exists():
        make_sample()
    url = f'http://127.0.0.1:{args.port}/'
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Откройте {url}', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
