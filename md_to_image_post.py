import sys, re, io, json, base64, requests
from pathlib import Path
from html.parser import HTMLParser
from PIL import Image, ImageDraw, ImageFont

URL = 'http://data.xiaoxinren.cn:9003/Api/Agent/PostDaily'

# --- HTML table extractor ---
class TableExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._in_table = self._in_tr = self._in_td = self._in_th = False
        self._current_table = []
        self._current_row = []
        self._current_cell = ""

    def handle_starttag(self, tag, attrs):
        if tag == 'table': self._in_table = True; self._current_table = []
        elif tag == 'tr': self._in_tr = True; self._current_row = []
        elif tag in ('td', 'th'): self._in_td = True; self._current_cell = ""

    def handle_endtag(self, tag):
        if tag == 'table': self._in_table = False; self.tables.append(self._current_table)
        elif tag == 'tr' and self._in_tr: self._in_tr = False; self._current_table.append(self._current_row)
        elif tag in ('td', 'th') and self._in_td:
            self._in_td = False; self._current_row.append(self._current_cell.strip())

    def handle_data(self, data):
        if self._in_td: self._current_cell += data

# --- layout helpers ---
def wrap_text(text, font, max_width, draw):
    words = text.split(' ')
    lines, current = [], ""
    for w in words:
        test = w if not current else current + ' ' + w
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current: lines.append(current)
            current = w
    if current: lines.append(current)
    return lines if lines else [' ']

def render_table(draw, rows, fonts, x, y, max_w, pad=6):
    if not rows: return y
    ncols = max(len(r) for r in rows)
    font = fonts['table']
    col_w = [0] * ncols
    for row in rows:
        for i, cell in enumerate(row):
            bbox = draw.textbbox((0, 0), cell, font=font)
            col_w[i] = max(col_w[i], bbox[2] - bbox[0] + pad * 2)
    total_w = sum(col_w)
    if total_w > max_w:
        scale = max_w / total_w
        col_w = [int(w * scale) for w in col_w]
    line_h = draw.textbbox((0, 0), 'X', font=font)[3] + pad
    for ri, row in enumerate(rows):
        cy = y
        cell_lines_list = []
        for i in range(ncols):
            cell_text = row[i] if i < len(row) else ''
            cell_lines_list.append(wrap_text(cell_text, font, col_w[i] - pad, draw))
        max_lines = max(len(cl) for cl in cell_lines_list)
        for li in range(max_lines):
            cx = x
            for i in range(ncols):
                txt = cell_lines_list[i][li] if li < len(cell_lines_list[i]) else ''
                draw.text((cx + pad, y + pad // 2), txt, font=font, fill='black')
                cx += col_w[i]
            y += line_h
        if ri == 0:
            y_sep = y
            draw.line([(x, y_sep), (x + sum(col_w), y_sep)], fill='#333', width=2)
        draw.line([(x, y), (x + sum(col_w), y)], fill='#ccc', width=1)
    draw.line([(x, y), (x + sum(col_w), y)], fill='#333', width=2)
    return y + pad

# --- main converter ---
def md_to_image(md_path: str, max_width=1100) -> bytes:
    text = Path(md_path).read_text(encoding='utf-8')

    font_paths = [
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/simsun.ttc',
    ]
    title_font = body_font = table_font = small_font = None
    for fp in font_paths:
        try: title_font = ImageFont.truetype(fp, 22); break
        except: pass
    for fp in font_paths:
        try: body_font = ImageFont.truetype(fp, 16); break
        except: pass
    for fp in font_paths:
        try: table_font = ImageFont.truetype(fp, 14); break
        except: pass
    for fp in font_paths:
        try: small_font = ImageFont.truetype(fp, 12); break
        except: pass
    if not body_font: body_font = ImageFont.load_default()

    fonts = {'title': title_font or body_font, 'body': body_font,
             'table': table_font or body_font, 'small': small_font or body_font}
    pad = 20
    draw_test = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    line_h = draw_test.textbbox((0, 0), 'X', font=fonts['body'])[3] + 4

    extractor = TableExtractor()
    extractor.feed(text)
    html_tables = extractor.tables

    text_no_html = re.sub(r'<table[^>]*>.*?</table>', '', text, flags=re.DOTALL)
    for i in range(len(html_tables)):
        text_no_html += f'\n\n<!--HTML_TABLE_{i}-->\n\n'

    lines = text_no_html.split('\n')
    blocks = []
    i = 0
    in_code = False; code_lines = []
    in_details = False; details_lines = []
    in_table = False; table_rows = []

    while i < len(lines):
        line = lines[i]
        if line.startswith('```'):
            if in_code: blocks.append(('code', '\n'.join(code_lines))); code_lines = []
            in_code = not in_code
            i += 1; continue
        if in_code: code_lines.append(line); i += 1; continue
        if '<details>' in line: in_details = True; details_lines = []; i += 1; continue
        if '</details>' in line and in_details:
            in_details = False; blocks.append(('details', '\n'.join(details_lines))); i += 1; continue
        if in_details: details_lines.append(line); i += 1; continue

        table_match = re.match(r'<!--HTML_TABLE_(\d+)-->', line)
        if table_match:
            blocks.append(('html_table', html_tables[int(table_match.group(1))]))
            i += 1; continue

        if '|' in line and line.strip().startswith('|'):
            if not in_table: table_rows = []
            in_table = True
            if not re.match(r'^[\|\s\-:]+$', line):
                table_rows.append([c.strip() for c in line.split('|')[1:-1]])
            i += 1
            if i < len(lines) and '|' not in lines[i]:
                blocks.append(('table', table_rows)); in_table = False
            continue
        if in_table: blocks.append(('table', table_rows)); in_table = False

        if line.startswith('# '): blocks.append(('h1', line[2:])); i += 1; continue
        if line.startswith('## '): blocks.append(('h2', line[3:])); i += 1; continue
        if line.startswith('### '): blocks.append(('h3', line[4:])); i += 1; continue
        if line.strip() in ('---', '***', '___'): blocks.append(('hr', '')); i += 1; continue

        if line.startswith('> '):
            bq_lines = []
            while i < len(lines) and lines[i].startswith('> '):
                bq_lines.append(lines[i][2:]); i += 1
            blocks.append(('blockquote', '\n'.join(bq_lines))); continue

        if not line.strip(): i += 1; continue

        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith('#') \
                and not lines[i].startswith('```') and '|' not in lines[i] \
                and not lines[i].startswith('> ') and '<details>' not in lines[i] \
                and not re.match(r'<!--HTML_TABLE_\d+-->', lines[i]) \
                and lines[i].strip() not in ('---', '***', '___'):
            para_lines.append(lines[i]); i += 1
        if para_lines: blocks.append(('para', '\n'.join(para_lines)))
        continue

    # calculate height
    y = pad
    for btype, content in blocks:
        font = fonts['body']
        if btype == 'h1':
            y += draw_test.textbbox((0, 0), content, font=fonts['title'])[3] + 8 + pad
        elif btype == 'h2':
            y += draw_test.textbbox((0, 0), content, font=fonts['title'])[3] + 4 + pad // 2
        elif btype == 'h3':
            y += draw_test.textbbox((0, 0), content, font=fonts['body'])[3] + 4 + pad // 2
        elif btype == 'hr': y += pad // 2
        elif btype == 'blockquote':
            for l in content.split('\n'):
                y += draw_test.textbbox((0, 0), l, font=fonts['small'])[3] + 2
            y += pad // 2
        elif btype == 'code':
            for l in content.split('\n'):
                y += draw_test.textbbox((0, 0), l, font=fonts['small'])[3] + 1
            y += pad // 2
        elif btype in ('table', 'html_table'):
            rows = content
            n_cols = max(len(r) for r in rows) if rows else 1
            col_w = (max_width - pad * 2) // n_cols
            cell_font = fonts['table']
            for row in rows:
                max_l = 0
                for cell in row:
                    wrapped = wrap_text(cell, cell_font, col_w - 6, draw_test)
                    max_l = max(max_l, len(wrapped))
                y += (draw_test.textbbox((0, 0), 'X', font=cell_font)[3] + 4) * max(max_l, 1)
            y += pad
        elif btype == 'details':
            for l in content.split('\n'):
                w = wrap_text(l, fonts['small'], max_width - pad * 2, draw_test)
                y += (draw_test.textbbox((0, 0), 'X', font=fonts['small'])[3] + 2) * len(w)
            y += pad // 2
        else:
            w = wrap_text(content, font, max_width - pad * 2, draw_test)
            y += line_h * len(w) + pad // 4

    img_h = y + pad
    img = Image.new('RGB', (max_width, img_h), 'white')
    draw = ImageDraw.Draw(img)

    # render
    y = pad
    for btype, content in blocks:
        font = fonts['body']
        if btype == 'h1':
            draw.text((pad, y), content, font=fonts['title'], fill='#111')
            y += draw.textbbox((0, 0), content, font=fonts['title'])[3] + 8
            draw.line([(pad, y), (max_width - pad, y)], fill='#333', width=2)
            y += pad
        elif btype == 'h2':
            draw.text((pad, y), content, font=fonts['title'], fill='#222')
            y += draw.textbbox((0, 0), content, font=fonts['title'])[3] + 4 + pad // 2
        elif btype == 'h3':
            draw.text((pad, y), content, font=fonts['body'], fill='#333')
            y += draw.textbbox((0, 0), content, font=fonts['body'])[3] + 4 + pad // 2
        elif btype == 'hr':
            draw.line([(pad, y), (max_width - pad, y)], fill='#ddd', width=1)
            y += pad // 2
        elif btype == 'blockquote':
            draw.rectangle([(pad, y), (pad + 3, y + line_h * len(content.split('\n')))], fill='#999')
            for l in content.split('\n'):
                draw.text((pad + 10, y), l, font=fonts['small'], fill='#555')
                y += draw.textbbox((0, 0), l, font=fonts['small'])[3] + 2
            y += pad // 2
        elif btype == 'code':
            for l in content.split('\n'):
                draw.text((pad + 4, y), l, font=fonts['small'], fill='#333')
                y += draw.textbbox((0, 0), l, font=fonts['small'])[3] + 1
            y += pad // 2
        elif btype in ('table', 'html_table'):
            y = render_table(draw, content, fonts, pad, y, max_width - pad * 2)
        elif btype == 'details':
            draw.text((pad, y), '▸ 展开', font=fonts['small'], fill='#888')
            y += draw.textbbox((0, 0), 'X', font=fonts['small'])[3] + 4
            for l in content.split('\n'):
                wrapped = wrap_text(l, fonts['small'], max_width - pad * 2 - 20, draw)
                for wl in wrapped:
                    draw.text((pad + 16, y), wl, font=fonts['small'], fill='#666')
                    y += draw.textbbox((0, 0), wl, font=fonts['small'])[3] + 2
            y += pad // 2
        else:
            wrapped = wrap_text(content, font, max_width - pad * 2, draw)
            for wl in wrapped:
                draw.text((pad, y), wl, font=font, fill='#222')
                y += line_h
            y += pad // 4

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


if __name__ == '__main__':
    md_path = sys.argv[1] if len(sys.argv) > 1 else 'daily_review/reports/advice/advice_2026-06-16.md'

    print(f'Converting {md_path} to PNG...')
    png_bytes = md_to_image(md_path)
    print(f'PNG size: {len(png_bytes)} bytes ({len(png_bytes)/1024:.1f} KB)')

    b64 = base64.b64encode(png_bytes).decode()
    print(f'Base64 size: {len(b64)} chars')

    print(f'POSTing to {URL}...')
    r = requests.post(URL, data=json.dumps(b64), headers={'Content-Type': 'application/json'}, timeout=60)
    print(f'status={r.status_code} body={r.text[:300]}')
    print('Done.')
