import os

def aggregate(pages_dir=None, out_file='siemens_docs_all.html'):
    if pages_dir is None:
        pages_dir = os.path.join(os.path.dirname(__file__), 'siemens_pages')
    if not os.path.isdir(pages_dir):
        print('PAGES DIR NOT FOUND:', pages_dir)
        return 2
    files = sorted([f for f in os.listdir(pages_dir) if f.lower().endswith('.html')])
    if not files:
        print('NO HTML FILES IN', pages_dir)
        return 3
    bodies = []
    for fn in files:
        path = os.path.join(pages_dir, fn)
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                txt = fh.read()
        except Exception as e:
            print('READ FAIL', path, e)
            continue
        low = txt.lower()
        bstart = low.find('<body')
        if bstart != -1:
            bstart = low.find('>', bstart)
            if bstart != -1:
                bend = low.rfind('</body>')
                if bend == -1:
                    bend = len(txt)
                body_html = txt[bstart+1:bend]
            else:
                body_html = txt
        else:
            body_html = txt
        section = f"<!-- START FILE: {fn} -->\n" + body_html + f"\n<!-- END FILE: {fn} -->\n"
        bodies.append(section)
    combined = ['<!doctype html>','<html>','<head>','<meta charset="utf-8"/>','<meta name="viewport" content="width=device-width, initial-scale=1"/>',f'<title>Aggregated Siemens Docs - {len(bodies)} pages</title>','</head>','<body>']
    combined.append('\n<hr/>\n'.join(bodies))
    combined.append('</body>')
    combined.append('</html>')
    try:
        with open(out_file, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(combined))
        print('WROTE', out_file, 'pages=', len(bodies))
        return 0
    except Exception as e:
        print('WRITE FAIL', e)
        return 4

if __name__ == '__main__':
    import sys
    pages_dir = None
    out_file = 'siemens_docs_all.html'
    if len(sys.argv) > 1:
        pages_dir = sys.argv[1]
    if len(sys.argv) > 2:
        out_file = sys.argv[2]
    rc = aggregate(pages_dir, out_file)
    sys.exit(rc)

