"""Main-figure numbering and inline placement from first prose citations."""
from copy import deepcopy
import re


REFERENCE = re.compile(
    r'\b(Fig(?:ure)?s?\.?)\s+(\d+[a-z]?(?:(?:\s*[–-]\s*|\s*,\s*(?:and\s+)?|\s+and\s+)\d+[a-z]?)*)\b'
)
NUMBER = re.compile(r'(\d+)([a-z]?)')
ORIGINAL_LABELS = {
    'figures/closed_axes/Figure_2_model_and_experiment.png': 1,
    'figures/closed_axes/Figure_1_domain_and_observed_storage.png': 2,
    'figures/current_Figure_3_observation_partitions.png': 3,
    'figures/current_Figure_4_multisite_seasonal_curves.png': 4,
    'figures/current_Figure_5_Wuqiao_calibration_testing.png': 5,
    'figures/closed_axes/Figure_3_policy_tradeoffs.png': 6,
    'figures/closed_axes/Figure_7_current_spatial_policy_outcomes.png': 7,
    'figures/closed_axes/Figure_8_continuous_class_irrigation.png': 8,
}


def tokens(expression):
    """Expand numerical ranges without losing a panel suffix."""
    matches = list(NUMBER.finditer(expression))
    output = []
    for index, match in enumerate(matches):
        number, suffix = int(match[1]), match[2]
        if index and re.fullmatch(r'\s*[–-]\s*', expression[matches[index-1].end():match.start()]):
            start = int(matches[index-1][1])
            if matches[index-1][2] or suffix or number < start:
                raise ValueError('Unsupported figure range: ' + expression)
            output.extend((value, '') for value in range(start + 1, number + 1))
        else:
            output.append((number, suffix))
    return output


def references(text):
    return [(number, suffix) for match in REFERENCE.finditer(text)
            for number, suffix in tokens(match[2])]


def renumber(text, mapping):
    def replace(match):
        original = tokens(match[2])
        values = [(mapping.get(number, number), suffix) for number, suffix in original]
        if values == original:
            return match[0]
        labels = [str(number) + suffix for number, suffix in values]
        if len(labels) == 1:
            joined = labels[0]
        elif len(labels) == 2:
            joined = ' and '.join(labels)
        else:
            joined = ', '.join(labels[:-1]) + ' and ' + labels[-1]
        return match[1] + ' ' + joined
    return REFERENCE.sub(replace, text)


def renumber_document(document, mapping):
    for block in document['blocks']:
        for key in ['caption', 'table_caption', 'table_note', 'text']:
            if isinstance(block.get(key), str):
                block[key] = renumber(block[key], mapping)
        block['paragraphs'] = [renumber(text, mapping) for text in block.get('paragraphs', [])]


def order_main_figures(article, supplement):
    """Keep section prose intact; record the exact paragraph for Word insertion."""
    article, supplement = deepcopy(article), deepcopy(supplement)
    figures = {}
    for block in article['blocks']:
        if 'figure' not in block:
            continue
        match = re.match(r'Figure (\d+)\.', block['caption'])
        if match is None or int(match[1]) in figures:
            raise ValueError('Missing or duplicate main-figure label')
        figures[int(match[1])] = block
    first = {}
    for block in article['blocks']:
        if 'figure' in block:
            continue
        for index, text in enumerate(block.get('paragraphs', [])):
            for number, _ in references(text):
                if number not in figures:
                    raise ValueError('Unknown main-figure reference: ' + str(number))
                if number not in first:
                    if not block.get('heading'):
                        raise ValueError('Figure citation has no section anchor')
                    first[number] = {'heading': block['heading'], 'index': index}
    if set(first) != set(figures):
        raise ValueError('Uncited main figures: ' + str(sorted(set(figures) - set(first))))
    mapping = {old: new for new, old in enumerate(first, 1)}
    renumber_document(article, mapping)
    renumber_document(supplement, mapping)
    anchors = {}
    for number in first:
        block = figures[number]
        block['after_paragraph'] = first[number]
        anchors.setdefault(first[number]['heading'], []).append(block)
    blocks = []
    for block in article['blocks']:
        if 'figure' in block:
            continue
        blocks.append(block)
        blocks.extend(anchors.get(block.get('heading'), []))
    article['blocks'] = blocks
    article['main_figure_order'] = 'first_prose_citation'
    return article, supplement, mapping


def original_references(text, figure_records):
    """Normalize reviewed captions back to the eight-figure authoring baseline."""
    mapping = {}
    for record in figure_records:
        match = re.match(r'Figure (\d+)\.', record['caption'])
        if match and record['figure'] in ORIGINAL_LABELS:
            mapping[int(match[1])] = ORIGINAL_LABELS[record['figure']]
    return renumber(text, mapping)
