"""Render the designed extraction diagram as SVG using only Python's stdlib.

Positions are deliberate: examples are separate objects, never long edge labels.
The companion Mermaid source remains available for editable Excalidraw import.
"""
from html import escape
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]
W, H = 1280, 1100
GREEN, PURPLE, RED, GRAY = '#39765b', '#7d56ae', '#b75d52', '#8393a0'
parts = []


def text(x, y, value, size=16, color='#293c48', weight='400', italic=False, mono=False, anchor='start'):
    family = 'Consolas, monospace' if mono else 'Arial, Helvetica, sans-serif'
    parts.append(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" fill="{color}" font-weight="{weight}" font-style="{"italic" if italic else "normal"}" text-anchor="{anchor}">{escape(value)}</text>')


def path(points, color=GRAY, marker=True, dashed=False):
    d = 'M ' + ' L '.join(f'{x} {y}' for x, y in points)
    tag = {'#39765b':'green','#7d56ae':'purple','#b75d52':'red','#8393a0':'gray'}[color]
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"'+
        (f' marker-end="url(#{tag})"' if marker else '') + (' stroke-dasharray="5 5"' if dashed else '') + '/>')


def processor(x,y,w,h,title,subtitle,llm=False):
    color=PURPLE if llm else GREEN
    fill='#f1eafb' if llm else '#eaf4ee'
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="15" fill="{fill}" stroke="{color}" stroke-opacity=".35" filter="url(#shadow)"/>')
    text(x+20,y+26,'LLM · SOL' if llm else 'PYTHON',11,color,'700')
    text(x+20,y+57,title,22,'#263c32' if not llm else '#44305d','600')
    for i,line in enumerate(subtitle):text(x+20,y+82+i*21,line,14,'#536a5c' if not llm else '#6b5b7e')


def obj(x,y,w,h,title,lines,color=GRAY,footer=None):
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#fff" stroke="{color}" stroke-opacity=".65" stroke-width="1.2" stroke-dasharray="5 5"/>')
    text(x+16,y+27,title,17,color,'500',italic=True)
    for i,line in enumerate(lines):text(x+16,y+53+i*22,line,13,'#536575',mono=True)
    if footer:text(x+16,y+h-15,footer,11,'#8a969f',italic=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--png',action='store_true',help='Also render PNG using optional Playwright')
    parser.add_argument('--browser',type=Path,help='Installed Chrome/Chromium executable for PNG rendering')
    args=parser.parse_args()
    if args.png and (not args.browser or not args.browser.is_file()):
        parser.error('--png requires --browser pointing to an installed Chrome/Chromium executable.')
    parts.clear()
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">')
    parts.append('<title id="title">Document recognition: ingestion, chunks and two feedback paths</title><desc id="desc">Green processing cards are Python, purple is the LLM. Dashed italic object cards contain selected examples. Red feedback corrects the same chunk. Green feedback supplies accumulated entity and geographic context to the next chunk. Project context is extracted in entity fields; Python constructs chunk_context.</desc>')
    parts.append('<defs><filter id="shadow" x="-15%" y="-20%" width="130%" height="150%"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#193b2b" flood-opacity=".055"/></filter>')
    for label,color in [('gray',GRAY),('green',GREEN),('purple',PURPLE),('red',RED)]:
        parts.append(f'<marker id="{label}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>')
    parts.append('</defs><rect width="1280" height="1100" fill="#f8fafb"/>')
    text(60,48,'01 / DOCUMENT RECOGNITION',12,'#7c8b94','700')
    text(60,91,'Ingest. Extract. Build context.',34,'#203a32','600')
    text(60,122,'One chunk at a time, with validated information carried forward.',16,'#72838e')
    for x,color,label in [(850,GREEN,'Python'),(960,PURPLE,'LLM'),(1050,GRAY,'Data object')]:
        parts.append(f'<rect x="{x}" y="58" width="13" height="13" rx="3" fill="{color}"/>')
        text(x+20,69,label,13,'#657782')

    # Thin connections sit behind the cards.
    path([(305,237),(335,237)])
    path([(565,237),(615,237)])
    path([(850,237),(900,237)])
    path([(1025,318),(1025,337),(425,337),(425,455)])
    text(682,319,'each chunk, in order',12,'#738591',anchor='middle')

    # Current chunk request.
    path([(550,520),(600,520)])
    path([(820,520),(870,520)])
    # LLM result and the return to the processor.
    path([(1030,590),(1030,635),(755,635),(755,663)],PURPLE)
    text(850,625,'response',12,PURPLE,anchor='middle')
    path([(610,760),(580,760),(580,561),(550,561)])
    text(599,657,'verify result',11,GRAY)
    # Failed validation: correction data, then same-chunk retry.
    path([(300,487),(270,487)],RED)
    path([(155,450),(155,378),(961,378),(961,455)],RED)
    text(631,362,'failed check → retry the same chunk',13,RED,'600',anchor='middle')
    # Successful validation: update context, then process next chunk.
    path([(350,590),(350,680),(262,680),(262,756)],GREEN)
    text(279,670,'validated',11,GREEN)
    path([(500,845),(550,845),(550,417),(1100,417),(1100,455)],GREEN)
    text(838,405,'valid result → enrich context → next chunk',13,GREEN,'600',anchor='middle')
    # Completed-document output.
    path([(550,584),(564,584),(564,950),(640,950),(640,982)])
    text(689,974,'all chunks processed',12,'#748590')

    processor(60,185,245,100,'Document ingestion',['Parse PDF, HTML or text'])
    obj(335,185,230,100,'Document',["name: 'Monte do Carmo'","text: '…'"])
    processor(615,185,235,100,'Create chunks',['Keep text + start/end offsets'])
    parts.append('<rect x="900" y="155" width="300" height="163" rx="13" fill="#f2f5f7" stroke="#e0e7eb"/>')
    text(916,177,'Chunk[]',16,GRAY,italic=True)
    for index,(label,y) in enumerate([('chunk 1',190),('chunk 2',232),('… chunk n',274)]):
        parts.append(f'<rect x="916" y="{y}" width="267" height="34" rx="8" fill="#fff" stroke="#a9b7c2" stroke-dasharray="4 4"/>')
        text(929,y+22,label,14,'#607684',italic=True)
        text(1020,y+22,'start · end · text',12,'#8a99a4',mono=True)

    processor(300,455,250,135,'Chunk processor',['Send chunk · verify result','Merge valid entities'])
    processor(870,455,320,135,'Extract locations',['Entity[] + spatial context'],llm=True)
    obj(600,480,220,90,'Request',['text','chunk_context'])
    obj(40,450,230,178,'Correction feedback',[
        'validation_feedback:',"  ['hole221: bad mention']",'previous_extraction:', '  {entities: […]}'],RED,footer='Previous response, when parsed')

    # Two examples of the same raw output array, not an invented context schema.
    parts.append('<rect x="590" y="663" width="640" height="278" rx="16" fill="#f3f0f8" stroke="#ddd5e9"/>')
    text(610,690,'LLM OUTPUT  ·  {entities: Entity[]}',12,PURPLE,'700')
    obj(610,705,290,217,'Entity · component',[
        "id: 'hole221'", "name: 'FSA-221'", "entity_kind:", "  'subaltern_location'", "semantic_unit: 'mdc'", "role: 'subject_component'", "verification_classes:", "  ['drilling_site']"])
    obj(920,705,290,217,'Entity · project context',[
        "id: 'mdc'", "name: 'Monte do Carmo'", "entity_kind: 'semantic_unit'", 'semantic_unit: null', "country_code: 'BR'", "jurisdiction: 'Tocantins'"],footer='Context is held in entity fields')

    obj(40,756,460,174,'chunk_context',[
        'existing_locations: [{id, name, …}, …]',
        'geographic_contexts: [{',
        "  country_code: 'BR', jurisdiction: 'Tocantins',",
        "  applies_to: ['c1-mdc', 'c1-hole221']",
        '}]'],GREEN,footer='Built by Python from accumulated, validated entities')
    obj(420,982,440,64,'Extraction.entities: Entity[]',[],footer='Validated locations → routing & coordinate resolution')
    text(60,1080,'Selected fields only. IDs become stable after Python merging. One correction attempt per failed chunk.',12,'#87949d')
    parts.append('</svg>')
    path_out=ROOT/'docs/diagrams/01-ingestion-extraction.svg'
    path_out.write_text('\n'.join(parts),encoding='utf-8')
    print('Generated',path_out.relative_to(ROOT))
    if args.png:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=str(args.browser),headless=True)
            page=browser.new_page(viewport={'width':W,'height':H},device_scale_factor=2)
            page.goto(path_out.resolve().as_uri(),wait_until='domcontentloaded')
            page.screenshot(path=str(path_out.with_suffix('.png')),timeout=20000)
            browser.close()
        print('Generated',path_out.with_suffix('.png').relative_to(ROOT))


if __name__=='__main__':main()
