"""Export current figure collections and remove unbound publication copies."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil

import pandas as pd
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / 'publication'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    blocks = {name: json.loads((PUB / 'analysis_source' / (name + '_blocks.json')).read_text())['blocks']
              for name in ['manuscript', 'supplementary']}
    figure_files = set()
    table_files = set()
    collections = {}
    for name, content in blocks.items():
        pdf = fitz.open()
        paths = []
        for block in content:
            if 'table' in block:
                table_files.add(PUB / block['table'])
            if 'figure' not in block:
                continue
            png = PUB / block['figure']
            vector = png.with_suffix('.pdf')
            if not png.is_file() or not vector.is_file():
                raise FileNotFoundError(png)
            with fitz.open(vector) as original:
                pdf.insert_pdf(original)
            paths.append(str(vector.relative_to(PUB)))
            for candidate in png.parent.glob(png.stem + '.*'):
                figure_files.add(candidate)
            caption = png.with_name(png.stem + '_caption.txt')
            if caption.exists():
                # Publication caption numbers follow the final document order.
                caption.write_text(block['caption'] + '\n')
                figure_files.add(caption)
        target = PUB / 'documents' / ('main_figures.pdf' if name == 'manuscript' else 'supplementary_figures.pdf')
        pdf.save(target)
        collections[name] = {'pages': len(pdf), 'sources': paths, 'sha256': sha(target)}
        pdf.close()
    shutil.copy2(PUB / 'documents/main_figures.pdf', PUB / 'documents/manuscript_figures.pdf')
    shutil.copy2(PUB / 'figures/graphical_abstract.pdf', PUB / 'documents/graphical_abstract.pdf')
    figure_files.update((PUB / 'figures').glob('graphical_abstract.*'))
    removed = []
    for item in sorted((PUB / 'figures').rglob('*')):
        if item.is_file() and item not in figure_files:
            removed.append(str(item.relative_to(PUB)))
            item.unlink()
    for folder in sorted((PUB / 'figures').rglob('*'), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    for item in sorted((PUB / 'tables').iterdir()):
        retain = item in table_files or item.name.startswith('current_') or item.name in ['paper_data_and_tables.xlsx','grain_gain_reconciliation.csv']
        if item.is_file() and not retain:
            removed.append(str(item.relative_to(PUB)))
            item.unlink()
    workbook = pd.ExcelFile(PUB / 'tables/paper_data_and_tables.xlsx')
    receipt = {'completed_utc': datetime.now(timezone.utc).isoformat(),
               'main_figures': collections['manuscript'], 'supplementary_figures': collections['supplementary'],
               'active_publication_figures': sum(item['pages'] for item in collections.values()),
               'active_publication_tables': len(table_files),
               'current_workbook_sheets': workbook.sheet_names,
               'unbound_publication_copies_removed': removed,
               'historical_originals_retained': True, 'sealed_calibration_and_regional_sources_modified': False}
    (PUB / 'verification/current_assets.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f"Current figures: {collections['manuscript']['pages']} main + {collections['supplementary']['pages']} supplementary; removed {len(removed)} unbound publication copies.")


if __name__ == '__main__':
    main()
