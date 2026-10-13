"""Render editable Mermaid sources using a pinned CLI; no app/model calls.

Requires Node.js / npx. Existing Chrome can avoid a bundled browser download:
python scripts/render_diagrams.py --browser "C:/path/to/chrome.exe"
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import re
import math
import sys

CLI_VERSION = '12.0.0'
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, help='Path to an installed Chrome/Chromium executable')
    parser.add_argument('--format', choices=['svg', 'png', 'pdf'], default='svg')
    parser.add_argument('--diagram', help='Render only this diagram stem, e.g. 01-ingestion-extraction')
    args = parser.parse_args()
    npx = shutil.which('npx.cmd' if os.name == 'nt' else 'npx')
    if not npx:
        parser.error('Node.js with npx is required only to regenerate the diagrams.')
    if args.browser and not args.browser.is_file():
        parser.error('Browser executable does not exist.')
    directory = ROOT / 'docs' / 'diagrams'
    env = dict(os.environ)
    if args.browser:
        env['PUPPETEER_SKIP_DOWNLOAD'] = '1'
    with tempfile.TemporaryDirectory(prefix='georeferencer-diagrams-') as temporary:
        config = Path(temporary) / 'puppeteer.json'
        config.write_text(json.dumps({'executablePath': str(args.browser.resolve())} if args.browser else {}), encoding='utf-8')
        sources=sorted(directory.glob('*.mmd'))
        if args.diagram:
            sources=[source for source in sources if source.stem==args.diagram]
            if not sources:parser.error('No Mermaid source matches that diagram name.')
        for source in sources:
            output = source.with_suffix('.' + args.format)
            if source.stem=='01-ingestion-extraction' and args.format in {'svg','png'}:
                command=[sys.executable,str(ROOT/'scripts/design_extraction_diagram.py')]
                if args.format=='png':
                    if not args.browser:parser.error('Designed PNG rendering requires --browser and optional Playwright.')
                    command.extend(['--png','--browser',str(args.browser.resolve())])
                subprocess.run(command,cwd=ROOT,check=True)
                continue
            command = [npx, '--yes', '--package', f'@mermaid-js/mermaid-cli@{CLI_VERSION}', 'mmdc',
                '--input', str(source), '--output', str(output),
                '--configFile', str(directory / 'mermaid-config.json'),
                '--puppeteerConfigFile', str(config), '--backgroundColor', 'white', '--size', '4000']
            subprocess.run(command, env=env, cwd=ROOT, check=True)
            if args.format == 'svg':
                # Preserve intrinsic dimensions: a max-height on long flowcharts
                # otherwise shrinks every label when opened as a browser document.
                svg = output.read_text(encoding='utf-8')
                box = re.search(r'viewBox="([^"]+)"', svg)
                if box:
                    _, _, width, height = map(float, box.group(1).split())
                    svg = re.sub(r'(<svg\b[^>]*?)width="[^"]*"',
                        lambda match: match[1] + f'width="{math.ceil(width)}" height="{math.ceil(height)}"', svg, count=1)
                    svg = re.sub(r'max-height:\s*[^;]+;', '', svg, count=1)
                    output.write_text(svg, encoding='utf-8')
            print(f'Rendered {output.relative_to(ROOT)}', flush=True)


if __name__ == '__main__':
    main()
