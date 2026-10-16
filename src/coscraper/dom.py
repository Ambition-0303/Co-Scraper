"""Query-aware HTML simplification and DOM reconstruction."""

from __future__ import annotations

import copy
import logging
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from functools import cache
from typing import Any

from lxml import etree, html
from lxml.etree import ParserError

# Constants defined locally to match the project's processing logic
# ---------------------------------------------------------------------------

INLINE_TAGS = {
    'map', 'optgroup', 'span', 'input', 'time', 'u', 'strong', 'small', 'sub',
    'samp', 'blink', 'b', 'code', 'nobr', 'strike', 'bdo', 'basefont', 'abbr', 'var', 'i', 'cccode-inline',
    's', 'pic', 'label', 'mark', 'object', 'ccmath-inline', 'svg',
    'button', 'a', 'font', 'dfn', 'sup', 'kbd', 'q', 'script', 'acronym', 'option', 'img', 'big', 'cite',
    'em', 'marked-tail', 'marked-text'
}

TABLE_TAGS_SET = {'caption', 'colgroup', 'col', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'br'}

NO_BLOCK_TAGS = {'math'}
NO_CALC_TEXT_TAGS = {'math', 'table'}

ATTR_PATTERNS_TO_REMOVE = {'nav'}
ATTR_INVISIBLE = {
    'display': 'none',
    'font-size': '0px',
    'color': 'transparent',
    'visibility': 'hidden',
    'opacity': '0',
}

TAIL_BLOCK_TAG = 'cc-alg-uc-text'
ITEM_ID_ATTR = '_item_id'
ANCESTOR_LINK_HREF_ATTR = 'ancestor-link-href'
INTERNAL_ATTRIBUTES = {
    ITEM_ID_ATTR,
    'data-uid',
    'cc-block-type',
    'cc-no-block',
    'cc-select',
}
SEMANTIC_ATTRIBUTES = {
    'action', 'alt', 'class', 'content', 'datetime', 'for', 'formaction',
    'headers', 'href', 'id', 'itemprop', 'itemscope', 'itemtype', 'name',
    'placeholder', 'poster', 'property', 'rel', 'role', 'src', 'srcset',
    'style', 'title', 'type', 'value', 'selected', 'checked',
    ANCESTOR_LINK_HREF_ATTR,
}
MEANINGFUL_ATTRIBUTES = {
    'action', 'alt', 'content', 'datetime', 'formaction', 'href',
    'placeholder', 'poster', 'src', 'srcset', 'title', 'value',
    ANCESTOR_LINK_HREF_ATTR,
}
RESOURCE_LINK_RELS = {
    'stylesheet', 'preload', 'prefetch', 'preconnect', 'dns-prefetch', 'icon',
}
VOID_TAGS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
}
INVALID_XML_CONTROL_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')

SHORT_CONTEXT_SIBLING_TAGS = {
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'label', 'legend', 'th', 'dt', 'caption',
}
SHORT_CONTEXT_MAX_TEXT_LENGTH = 200


# ---------------------------------------------------------------------------
# DOM utilities
# ---------------------------------------------------------------------------

def _parse_document(html_text: str) -> html.HtmlElement:
    """Parse every pipeline view with the same lxml configuration."""
    parser = html.HTMLParser(
        collect_ids=False,
        encoding='utf-8',
        remove_blank_text=False,
        remove_comments=True,
        remove_pis=True,
    )
    cleaned = _remove_xml_declaration(html_text or '')
    cleaned = INVALID_XML_CONTROL_RE.sub('', cleaned)
    try:
        root = html.document_fromstring(cleaned.encode('utf-8'), parser=parser)
    except (ParserError, TypeError, ValueError) as exc:
        raise ValueError('Failed to parse HTML document') from exc

    root.text = None
    for child in list(root):
        if not isinstance(child.tag, str) or child.tag.lower() not in {'head', 'body'}:
            root.remove(child)
            continue
        child.tail = None
    return root


def _serialize_document(root: html.HtmlElement) -> str:
    """Serialize every pipeline view with the same lxml settings."""
    return html.tostring(
        root,
        encoding='unicode',
        method='html',
        pretty_print=True,
    )


def _add_data_uids(dom: html.HtmlElement) -> None:
    for node in dom.iter():
        try:
            node.set('data-uid', str(uuid.uuid4()))
        except TypeError:
            pass


def _remove_all_uids(dom: html.HtmlElement) -> None:
    for node in dom.iter():
        if 'data-uid' in node.attrib:
            del node.attrib['data-uid']


def _build_uid_map(dom: html.HtmlElement) -> dict[str, html.HtmlElement]:
    return {node.get('data-uid'): node for node in dom.iter() if node.get('data-uid')}


def _remove_xml_declaration(html_string: str) -> str:
    return re.sub(r'<\?xml\s+.*?\??>', '', html_string, flags=re.DOTALL)


def _is_semantic_attribute(name: str) -> bool:
    name = name.lower()
    return (
        name in SEMANTIC_ATTRIBUTES
        or name.startswith('aria-')
        or name.startswith('data-')
        or name.endswith(':href')
    )


def _project_nearest_ancestor_link(
    target: html.HtmlElement,
    path: list[html.HtmlElement],
) -> None:
    """Project at most one nearest ancestor a[href] onto a model fragment."""
    if target.tag.lower() == "a":
        return
    for ancestor in reversed(path[:-1]):
        if not isinstance(ancestor.tag, str):
            continue
        if ancestor.tag.lower() != 'a':
            continue

        href = ancestor.get('href')
        if href is None or not href.strip():
            continue

        target.set(ANCESTOR_LINK_HREF_ATTR, href)
        return




def _is_useful_head_node(node: html.HtmlElement) -> bool:
    """Keep only title, canonical URL, and the standard description meta."""
    if not isinstance(node.tag, str):
        return False
    tag = node.tag.lower()

    if tag == 'title':
        return bool(''.join(node.itertext()).strip())

    if tag == 'meta':
        return bool(
            (node.get('content') or '').strip()
            and (node.get('name') or '').strip().casefold() == 'description'
        )

    if tag == 'link':
        rels = set((node.get('rel') or '').lower().split())
        return bool(
            (node.get('href') or '').strip()
            and 'canonical' in rels
        )

    return False


def _sanitize_head(root: html.HtmlElement) -> None:
    """Keep only the minimal head context used by downstream extraction."""
    heads = root.xpath('//head')
    if not heads:
        return

    head = heads[0]
    for child in list(head):
        if not _is_useful_head_node(child):
            child.drop_tree()


def post_process_html(html_content: str) -> str:
    """Post-process simplified HTML."""
    if not html_content:
        return html_content

    # Normalize whitespace outside tags while preserving tag markup.
    def replace_outside_tag_space(match):
        """Replace only whitespace runs outside tags."""
        if match.group(1):  # Tag markup
            return match.group(1)
        elif match.group(2):  # Text outside tags
            # Collapse whitespace runs outside tags to single spaces.
            return re.sub(r'\s+', ' ', match.group(2))
        return match.group(0)  # Return the complete match by default

    # Match tag markup and text outside tags with one regex.
    html_content = re.sub(r'(<[^>]+>)|([^<]+)', replace_outside_tag_space, html_content)

    return html_content.strip()


def _remove_tags(dom: html.HtmlElement) -> None:
    """Remove resource noise while retaining extraction-bearing nodes."""
    _sanitize_head(dom)

    # Apply the same resource cleanup to any style/script/link nodes that
    # appear outside head in malformed or framework-generated HTML.
    for node in list(dom.xpath('.//style')):
        node.drop_tree()

    for node in list(dom.xpath('.//script')):
        node.drop_tree()

    for node in list(dom.xpath('.//link')):
        rels = set((node.get('rel') or '').lower().split())
        if rels & RESOURCE_LINK_RELS:
            node.drop_tree()




def is_data_table(table_element: html.HtmlElement) -> bool:
    """Determine whether a table is a data table or a layout table.

    Return True for data tables and False for layout tables.
    Nodes in nested tables do not affect the current table's classification.
    """

    def belongs_to_current_table(node: html.HtmlElement) -> bool:
        """Check whether the node's nearest table ancestor is this table."""
        ancestor = node.getparent()
        while ancestor is not None:
            if ancestor.tag == "table":
                return ancestor is table_element
            ancestor = ancestor.getparent()
        return False

    def nodes_in_current_table(xpath: str) -> list[html.HtmlElement]:
        """Evaluate XPath while excluding matches from nested tables."""
        return [
            node
            for node in table_element.xpath(xpath)
            if belongs_to_current_table(node)
        ]

    def is_ignored_decoration(node: html.HtmlElement) -> bool:
        """Ignore controls and hints that do not change table data semantics."""
        current = node

        while current is not None and current is not table_element:
            tag = current.tag if isinstance(current.tag, str) else ""
            role = (current.get("role") or "").strip().lower()
            classes = set((current.get("class") or "").lower().split())

            if tag in {"button", "svg", "path"}:
                return True

            if role in {"tooltip", "presentation"}:
                return True

            if classes.intersection({
                "button-and-tooltip",
                "more-info",
                "tooltip",
                "visually-hidden",
                "left-corner",
            }):
                return True

            current = current.getparent()

        return False

    # 1. Prioritize explicit layout-table declarations.
    role = (table_element.get("role") or "").strip().lower()
    if role in {"presentation", "none"}:
        return False

    # 2. Explicit data-table markers.
    if role in {"table", "grid", "treegrid"}:
        return True

    if table_element.get("data-table") is not None:
        return True

    # 3. The summary attribute usually describes a data table's content and structure.
    summary = (table_element.get("summary") or "").strip()
    if summary:
        normalized_summary = summary.lower()

        # Avoid treating a summary explicitly marked as layout as a data signal.
        layout_markers = {
            "layout",
            "layout table",
            "presentation",
            "presentational",
        }
        if normalized_summary not in layout_markers:
            return True

    # 4. A caption is a strong data-table signal.
    if nodes_in_current_table(".//caption"):
        return True

    # 5. colgroup/col elements indicate an explicit column structure.
    if (
        nodes_in_current_table(".//colgroup")
        or nodes_in_current_table(".//col")
    ):
        return True

    # 6. th elements are explicit row or column headers.
    if nodes_in_current_table(".//th"):
        return True

    # 7. The headers attribute explicitly associates headers with data cells.
    if nodes_in_current_table(
        ".//*[self::td or self::th][@headers]"
    ):
        return True

    # 8. The scope attribute also indicates an explicit header relationship.
    if nodes_in_current_table(".//th[@scope]"):
        return True

    # 9. Check for key-value metadata tables, for example:
    #
    #    Subjects:  Artificial Intelligence
    #    Cite as:   arXiv:2401.00588
    #
    rows = nodes_in_current_table(".//tr")
    structured_row_count = 0
    key_value_row_count = 0

    for row in rows:
        cells = [
            cell
            for cell in row.xpath("./th | ./td")
            if belongs_to_current_table(cell)
        ]

        if len(cells) < 2:
            continue

        structured_row_count += 1
        first_cell = cells[0]
        first_text = " ".join(first_cell.itertext()).strip()
        first_classes = set(
            (first_cell.get("class") or "").lower().split()
        )

        is_label_cell = (
            first_cell.tag == "th"
            or first_text.rstrip().endswith(":")
            or bool(first_classes.intersection({
                "label",
                "key",
                "field",
                "field-name",
                "header",
                "row-header",
            }))
        )

        if is_label_cell:
            key_value_row_count += 1

    # Classify tables with at least two label-value rows as data tables.
    if structured_row_count >= 2 and key_value_row_count >= 2:
        return True

    # 10. Preserve the original classification logic:
    # A table containing only standard table or inline elements is a data table.
    owned_cells = nodes_in_current_table(
        ".//*[self::td or self::th]"
    )

    if not owned_cells:
        return False

    for node in table_element.iterdescendants():
        if not isinstance(node.tag, str):
            continue

        # Skip nested tables and their contents.
        nearest_table = node.getparent()
        while nearest_table is not None and nearest_table.tag != "table":
            nearest_table = nearest_table.getparent()

        if nearest_table is not table_element:
            continue

        if is_ignored_decoration(node):
            continue

        if node.tag in TABLE_TAGS_SET:
            continue

        if node.tag in INLINE_TAGS:
            continue

        # Ordinary block structures such as div, section, article, or p
        # indicate that the table is more likely used for page layout.
        return False

    return True

def has_non_listitem_children(list_element):
    """Check whether a list has direct children that are not list items.

    :param list_element: lxml element (ul, ol, or dl)
    :return: True if a disallowed direct child exists, otherwise False
    """

    # Determine allowed child tags from the list type.
    if list_element.tag in ['ul', 'ol']:
        allowed_tags = {'li'}
    elif list_element.tag == 'dl':
        allowed_tags = {'dt', 'dd'}

    # Use XPath to find disallowed direct children.
    # For <ul>, find direct children other than <li>.
    # For <dl>, find direct children other than <dt> or <dd>.
    exclude_conditions = ' and '.join([f"name()!='{tag}'" for tag in allowed_tags])
    disallowed_children_xpath = f'./*[{exclude_conditions}]'

    if list_element.xpath(disallowed_children_xpath):
        return True

    # Check for non-whitespace text nodes.
    text_children = list_element.xpath('./text()')
    non_whitespace_text = any(text.strip() for text in text_children)

    return non_whitespace_text


def is_meaningful_content(element) -> bool:
    """Check whether an element has meaningful content beyond placeholders."""
    text = (element.text or '').strip()
    tail = (element.tail or '').strip()

    # An ellipsis without tail text does not count as content.
    placeholder_only_text = text == '...' and not tail

    if text and not placeholder_only_text:
        return True

    # Meaningful attributes can make a node significant even if its text is an ellipsis.
    if any(
        (element.get(attribute) or '').strip()
        for attribute in MEANINGFUL_ATTRIBUTES
    ):
        return True

    # Descendants may contain meaningful text or attributes.
    for child in element:
        if is_meaningful_content(child):
            return True

    if tail:
        return True

    return False


@cache
def _is_writable_attribute_name(name: str) -> bool:
    """Return whether lxml can write this parsed HTML attribute name back."""
    probe = html.Element('span')
    try:
        probe.attrib.update({name: ''})
    except (TypeError, ValueError):
        return False
    return True


def clean_attributes(element, short_src=False):
    """Keep useful model attributes and bound only opaque long values."""
    kept = {}
    for name, value in element.attrib.items():
        if name in INTERNAL_ATTRIBUTES:
            continue
        if not _is_writable_attribute_name(name):
            continue
        if _is_semantic_attribute(name) or len(value) <= 500:
            kept[name] = value
        else:
            kept[name] = value[:500] + '...'
    element.attrib.clear()
    element.attrib.update(kept)

    # Recursively process child elements
    for child in element:
        clean_attributes(child, short_src=short_src)

def truncate_attributes(element, threshold=500, keep_length=500):
    """Truncate only unknown long attributes; semantic values stay lossless."""
    for el in element.iter():
        for attr_name, attr_value in list(el.attrib.items()):
            if attr_name in INTERNAL_ATTRIBUTES or _is_semantic_attribute(attr_name):
                continue
            if len(str(attr_value)) > threshold:
                el.set(attr_name, str(attr_value)[:keep_length] + "...")

def _should_remove_element(element: html.HtmlElement) -> bool:
    class_name = element.get('class', '')
    id_name = element.get('id', '')
    if class_name in ATTR_PATTERNS_TO_REMOVE or id_name in ATTR_PATTERNS_TO_REMOVE:
        parent = element.getparent()
        if parent is not None and parent.tag == 'body':
            return True
    style_attr = element.get('style', '')
    if style_attr:
        for part in style_attr.split(';'):
            if ':' not in part:
                continue
            k, _, v = part.partition(':')
            k, v = k.strip(), v.strip()
            if ATTR_INVISIBLE.get(k) == v:
                return True
    # parent = element.getparent()
    # if parent is not None and parent.tag == 'details':
    #     if element.tag == 'summary':
    #         return False
    #     return parent.get('open') is None
    return False

def _replace_specific_elements(element):
    """
    Replace matched elements' contents with "..." instead of removing their tags.
    This preserves the DOM tree's physical structure (hierarchy and node count).
    """
    # 1. Recursively process child elements
    # Children can be processed before replacement or skipped for efficiency.
    if _should_remove_element(element):
        # --- Perform the replacement ---
        had_content = bool(
            (element.text and element.text.strip())
            or len(element) > 0
        )
        
        # Remove all child tags so no nested structure remains.
        for child in list(element):
            element.remove(child)
        
        # Void elements cannot contain text. Keep them empty instead of
        # producing invalid fragments such as <hr>...</hr>.
        if isinstance(element.tag, str) and element.tag.lower() in VOID_TAGS:
            element.text = None
        else:
            # Replace the element's text content with "..."
            element.text = "..." if had_content else None
        
        # Replacement removed all children, so there is no need to recurse into them.
        return 

    # 2. Otherwise, recursively process the children normally.
    for child in list(element.iterchildren()):
        _replace_specific_elements(child)


LIST_TAGS = {'ul', 'ol', 'dl', 'select'}
LIST_REPEAT_COMPRESSION_THRESHOLD = 10
LIST_STATE_CLASSES = {
    'active', 'selected', 'disabled', 'current',
    'first', 'last', 'odd', 'even',
}
TABLE_COMPRESSION_THRESHOLD = 10
TABLE_SAMPLE_ROWS_PER_REGION = 3
TABLE_STRUCTURAL_VALUE_ATTRIBUTES = {
    'rowspan', 'colspan', 'scope', 'headers',
    'role', 'class', 'style',
}


def _structure_signature(node: html.HtmlElement, depth: int = 0) -> tuple[Any, ...]:
    """Describe DOM shape while ignoring item-specific text and URL values."""
    if not isinstance(node.tag, str):
        return ('unknown',)

    tag = node.tag.lower()
    attribute_names = tuple(sorted(
        name
        for name in node.attrib
        if name not in INTERNAL_ATTRIBUTES
        and not name.startswith('data-')
        and name != 'class'
    ))
    class_tokens = tuple(sorted(
        token
        for token in (node.get('class') or '').lower().split()
        if token not in LIST_STATE_CLASSES
    ))
    has_text = bool((node.text or '').strip())

    # Nested-list length is data, not part of the surrounding item schema.
    if depth > 0 and tag in LIST_TAGS:
        return tag, attribute_names, class_tokens, 'nested-list'
    if depth >= 8:
        return tag, attribute_names, class_tokens, has_text

    children = tuple(
        _structure_signature(child, depth + 1)
        for child in node
        if isinstance(child.tag, str)
    )
    return tag, attribute_names, class_tokens, has_text, children


def _list_entries(element: html.HtmlElement) -> list[list[html.HtmlElement]]:
    """Return logical entries, grouping one dl term with its descriptions."""
    children = list(element)
    if element.tag in {'ul', 'ol'}:
        return [[child] for child in children if child.tag == 'li']
    if element.tag == 'select':
        return [[child] for child in children if child.tag == 'option']
    if element.tag != 'dl':
        return []

    entries: list[list[html.HtmlElement]] = []
    current_entry: list[html.HtmlElement] = []
    for child in children:
        if child.tag == 'dt':
            if current_entry:
                entries.append(current_entry)
            current_entry = [child]
        elif child.tag == 'dd':
            if current_entry:
                current_entry.append(child)
            else:
                entries.append([child])
    if current_entry:
        entries.append(current_entry)
    return entries


def _consecutive_runs(indices: set[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for index in sorted(indices):
        if not runs or index != runs[-1][-1] + 1:
            runs.append([index])
        else:
            runs[-1].append(index)
    return runs


def _list_ellipsis(element: html.HtmlElement, omitted_count: int) -> html.HtmlElement:
    if element.tag in {'ul', 'ol'}:
        placeholder = etree.Element('li')
    elif element.tag == 'dl':
        placeholder = etree.Element('dd')
    else:
        placeholder = etree.Element('option')
        placeholder.set('disabled', 'disabled')
    placeholder.set('data-list-ellipsis', 'true')
    placeholder.set('data-omitted-count', str(omitted_count))
    placeholder.text = '...'
    return placeholder


def simplify_list(element: html.HtmlElement) -> None:
    """Compress only entry structures repeated more than the threshold."""
    if element.tag not in LIST_TAGS:
        for child in list(element):
            simplify_list(child)
        return

    entries = _list_entries(element)
    original_count = len(entries)
    if original_count > LIST_REPEAT_COMPRESSION_THRESHOLD:
        entries_by_signature: dict[tuple[Any, ...], list[int]] = defaultdict(list)
        for index, entry in enumerate(entries):
            signature = tuple(_structure_signature(node) for node in entry)
            entries_by_signature[signature].append(index)

        keep_indices: set[int] = set()
        for indices in entries_by_signature.values():
            if len(indices) <= LIST_REPEAT_COMPRESSION_THRESHOLD:
                keep_indices.update(indices)
                continue
            middle = indices[len(indices) // 2]
            keep_indices.update({
                indices[0], indices[1], middle, indices[-2], indices[-1],
            })

        omitted_indices = set(range(original_count)) - keep_indices
        if omitted_indices:
            element.set('data-original-item-count', str(original_count))
            for run in _consecutive_runs(omitted_indices):
                first_node = entries[run[0]][0]
                first_node.addprevious(_list_ellipsis(element, len(run)))
                for entry_index in run:
                    for node in entries[entry_index]:
                        parent = node.getparent()
                        if parent is not None:
                            parent.remove(node)

    for child in list(element):
        simplify_list(child)


def _strict_table_structure_signature(
    node: html.HtmlElement,
) -> tuple[Any, ...]:
    """Describe exact row structure while ignoring record-specific values."""
    attributes = []
    for name, value in sorted(node.attrib.items()):
        if name in INTERNAL_ATTRIBUTES:
            continue
        if name in TABLE_STRUCTURAL_VALUE_ATTRIBUTES:
            attributes.append((name, value))
        else:
            attributes.append((name, None))

    return (
        node.tag.lower(),
        tuple(attributes),
        bool((node.text or '').strip()),
        bool((node.tail or '').strip()),
        tuple(
            _strict_table_structure_signature(child)
            for child in node
            if isinstance(child.tag, str)
        ),
    )


def _sample_table_row_indices(row_count: int) -> set[int]:
    """Select first, middle, and last row positions within one structure."""
    size = TABLE_SAMPLE_ROWS_PER_REGION
    middle_start = max(0, (row_count - size) // 2)
    return {
        *range(0, min(size, row_count)),
        *range(middle_start, min(middle_start + size, row_count)),
        *range(max(0, row_count - size), row_count),
    }


def _table_column_count(row: html.HtmlElement) -> int:
    column_count = 0
    for cell in row.xpath('./td | ./th'):
        try:
            column_count += max(1, int(cell.get('colspan', '1')))
        except (TypeError, ValueError):
            column_count += 1
    return max(1, column_count)


def _table_ellipsis_row(
    column_count: int,
    omitted_count: int,
    structure_id: int,
) -> html.HtmlElement:
    row = etree.Element('tr')
    row.set('data-table-ellipsis', 'true')
    row.set('data-table-structure', str(structure_id))
    row.set('data-omitted-row-count', str(omitted_count))

    cell = etree.SubElement(row, 'td')
    cell.set('colspan', str(max(1, column_count)))
    cell.text = f'... ({omitted_count} rows omitted)'
    return row


def _select_rows_by_global_structure(
    rows: list[html.HtmlElement],
) -> tuple[set[int], list[int]]:
    """Sample repeated structures globally without changing DOM row order."""
    signature_ids: dict[tuple[Any, ...], int] = {}
    rows_by_structure: dict[int, list[int]] = defaultdict(list)
    row_structure_ids = []

    for row_index, row in enumerate(rows):
        signature = _strict_table_structure_signature(row)
        if signature not in signature_ids:
            signature_ids[signature] = len(signature_ids) + 1
        structure_id = signature_ids[signature]
        row_structure_ids.append(structure_id)
        rows_by_structure[structure_id].append(row_index)

    keep_indices = set()
    for structure_indices in rows_by_structure.values():
        if len(structure_indices) <= TABLE_COMPRESSION_THRESHOLD:
            keep_indices.update(structure_indices)
            continue
        sampled_positions = _sample_table_row_indices(len(structure_indices))
        keep_indices.update(
            structure_indices[position]
            for position in sampled_positions
        )

    return keep_indices, row_structure_ids


def _consecutive_omitted_table_runs(
    omitted_indices: set[int],
    structure_ids: list[int],
) -> list[list[int]]:
    """Keep separate ellipses for adjacent omitted rows of different shapes."""
    runs: list[list[int]] = []
    for index in sorted(omitted_indices):
        if (
            not runs
            or index != runs[-1][-1] + 1
            or structure_ids[index] != structure_ids[runs[-1][-1]]
        ):
            runs.append([index])
        else:
            runs[-1].append(index)
    return runs


def _compress_one_data_table_for_model(table: html.HtmlElement) -> None:
    """Compress globally repeated row shapes in the model-only table copy."""
    if not is_data_table(table):
        return

    bodies = table.xpath('./tbody')
    if len(bodies) > 1:
        return

    row_parent = bodies[0] if bodies else table
    rows = [child for child in row_parent if child.tag == 'tr']

    # A leading header inside tbody/direct rows stays complete. Thead and tfoot
    # are outside this data-row collection and therefore also stay complete.
    while rows and rows[0].xpath('./th'):
        rows.pop(0)
    if not rows or any(row.xpath('./th') for row in rows):
        return

    keep_indices, structure_ids = _select_rows_by_global_structure(rows)
    omitted_indices = set(range(len(rows))) - keep_indices
    if not omitted_indices:
        return

    table.set('data-table-compressed', 'true')
    table.set('data-original-row-count', str(len(rows)))
    table.set('data-retained-row-count', str(len(keep_indices)))

    for run in _consecutive_omitted_table_runs(
        omitted_indices,
        structure_ids,
    ):
        first_index = run[0]
        rows[first_index].addprevious(
            _table_ellipsis_row(
                column_count=_table_column_count(rows[first_index]),
                omitted_count=len(run),
                structure_id=structure_ids[first_index],
            )
        )
        for index in run:
            parent = rows[index].getparent()
            if parent is not None:
                parent.remove(rows[index])


def compress_data_tables_for_model(root: html.HtmlElement) -> None:
    """Bound eligible data tables without modifying mapped/canonical views."""
    tables = root.xpath('self::table | .//table')
    for table in reversed(tables):
        _compress_one_data_table_for_model(table)


def truncate_html_element_selective(element, max_length, ellipsis='...', exclude_tags=None):
    """
    Truncate the text content within the lxml element and exclude the text within the specified label from being included in the length limit,
    But the tail outside the specified label is included in the length limit, and an ellipsis is added after the text of the length limit,
    And ensure that ellipses are not inserted inside the excluded labels.

    params:
        element (lxml.etree.Element): The lxml elements to be processed
        max_length (int): Maximum allowed text length (excluding the length of specified labels)
        ellipsis (str): The omitted symbol added after truncation defaults to '...'
        exclude_tags (list): List of label names not included in length statistics, such as ['math ',' script ',' style ']

    return:
        lxml.etree.Element: Processed elements
    """
    if exclude_tags is None:
        exclude_tags = set()

    def _calculate_text_length(node):
        """Calculate the effective text length of the node and its descendants (excluding text within the specified label)"""
        total_length = 0

        if node.text and not _is_excluded(node):
            total_length += len(node.text)

        for child in node:
            total_length += _calculate_text_length(child)

        if node.tail:
            total_length += len(node.tail)
        return total_length

    def _is_excluded(node):
        """Check if the node or its ancestor node is in the exclusion list"""
        current = node
        while current is not None:
            if current.tag in exclude_tags:
                return True
            current = current.getparent()
        return False

    current_length = [0]
    ellipsis_added = [False]
    nodes_to_process = []

    def _collect_text_nodes(node):
        """
        Recursive collection of all text node information that needs to be processed (including text and tail)
        Simultaneously mark whether the node is allowed to be modified (not included in the exclusion label)
        """
        if node.text and not _is_excluded(node):
            nodes_to_process.append({
                'type': 'text',
                'node': node,
                'original_text': node.text,
                'can_modify': not _is_inside_excluded_tag(node)
            })

        for child in node:
            _collect_text_nodes(child)

        if node.tail:
            nodes_to_process.append({
                'type': 'tail',
                'node': node,
                'original_text': node.tail,
                'can_modify': not _is_inside_excluded_tag(node)
            })

    def _is_inside_excluded_tag(node):
        """Check if the node is located inside the excluded label"""
        return _is_excluded(node.getparent()) if node.getparent() is not None else False

    def _process_text_nodes():
        """Process the collected text nodes, perform truncation and ellipsis addition"""
        for node_info in nodes_to_process:
            if ellipsis_added[0]:
                if node_info['type'] == 'text':
                    node_info['node'].text = None
                else:
                    node_info['node'].tail = None
                continue

            text_len = len(node_info['original_text'])
            if current_length[0] + text_len <= max_length:
                current_length[0] += text_len
            else:
                if node_info['can_modify']:
                    remaining = max_length - current_length[0]
                    truncated_text = node_info['original_text'][:remaining] + ellipsis

                    if node_info['type'] == 'text':
                        node_info['node'].text = truncated_text
                    else:
                        node_info['node'].tail = truncated_text

                    current_length[0] = max_length
                    ellipsis_added[0] = True

                    _mark_truncation_point(node_info['node'])
                else:
                    current_length[0] += text_len

    def _mark_truncation_point(truncate_node):
        """Mark truncation points and clean up subsequent content"""
        parent = truncate_node.getparent()
        if parent is not None:
            children = list(parent)
            try:
                index = children.index(truncate_node)
                for sibling in children[index + 1:]:
                    parent.remove(sibling)
            except ValueError:
                pass

        _clean_ancestors_following_siblings(truncate_node)

    def _clean_ancestors_following_siblings(node):
        """Recursively clean up the subsequent sibling nodes of all ancestor nodes"""
        parent = node.getparent()
        if parent is None:
            return

        grandparent = parent.getparent()
        if grandparent is None:
            return

        children = list(grandparent)
        try:
            index = children.index(parent)
            for sibling in children[index + 1:]:
                grandparent.remove(sibling)
        except ValueError:
            pass

        _clean_ancestors_following_siblings(parent)

    # 1. First, calculate the total text length
    total_text_length = _calculate_text_length(element)

    # 2. If the total length does not exceed the limit, return directly
    if total_text_length <= max_length:
        return element

    # 3. Collect and process text nodes
    _collect_text_nodes(element)
    _process_text_nodes()

    return element


def _truncate_model_paragraph(
    root: html.HtmlElement,
    max_length: int,
) -> None:
    """Truncate retained list entries independently, or one normal paragraph."""
    if root.tag not in LIST_TAGS:
        truncate_html_element_selective(
            root,
            max_length=max_length,
            exclude_tags=NO_CALC_TEXT_TAGS,
        )
        return

    for entry in _list_entries(root):
        for node in entry:
            if node.get('data-list-ellipsis') == 'true':
                continue
            parent = node.getparent()
            if parent is None:
                continue
            truncated_node = copy.deepcopy(node)
            truncate_html_element_selective(
                truncated_node,
                max_length=max_length,
                exclude_tags=NO_CALC_TEXT_TAGS,
            )
            parent.replace(node, truncated_node)


# ---------------------------------------------------------------------------
# Paragraph extraction (simplified core logic matching the simplify pipeline)
# ---------------------------------------------------------------------------

def _parse_paragraph_fragment(fragment: str) -> html.HtmlElement:
    """Parse a paragraph while preserving html/head/body wrapper elements."""
    match = re.match(r'\s*<(html|head|body)\b', fragment, flags=re.IGNORECASE)
    if not match:
        return html.fragment_fromstring(fragment, create_parent=False)

    tag = match.group(1).lower()
    document = _parse_document(fragment)
    if tag == 'html':
        return document

    nodes = document.xpath(f'//{tag}')
    if not nodes:
        raise ParserError(f'Could not recover paragraph root: {tag}')
    return nodes[0]


def _normalize_model_paragraph_root(root: html.HtmlElement) -> html.HtmlElement:
    """Avoid nesting document-shell tags inside the simplified body."""
    if not isinstance(root.tag, str) or root.tag.lower() not in {
        'html', 'head', 'body',
    }:
        return root

    wrapper = html.Element('div')
    wrapper.attrib.update(root.attrib)
    wrapper.text = root.text
    for child in list(root):
        root.remove(child)
        wrapper.append(child)
    return wrapper




def _build_model_head(
    original_dom: html.HtmlElement,
    cutoff_length: int,
) -> html.HtmlElement | None:
    """Create the ID-free, model-bounded copy of the sanitized head."""
    heads = original_dom.xpath('//head')
    if not heads:
        return None

    model_head = copy.deepcopy(heads[0])
    for node in model_head.iter():
        for attribute in INTERNAL_ATTRIBUTES:
            node.attrib.pop(attribute, None)
    return model_head


def _remove_head_from_processing_dom(root: html.HtmlElement) -> None:
    """Keep head out of paragraph extraction and useful-ID selection."""
    for head in list(root.xpath('//head')):
        head.drop_tree()


def _inject_model_head(
    simplified_html: str,
    model_head: html.HtmlElement | None,
) -> str:
    """Place rule-selected head information directly in simplified HTML."""
    root = _parse_document(simplified_html)
    heads = root.xpath('//head')
    if heads:
        head = heads[0]
    else:
        head = html.Element('head')
        root.insert(0, head)

    for child in list(head):
        head.remove(child)

    if model_head is not None:
        for child in model_head:
            head.append(copy.deepcopy(child))

    if not head.xpath('./meta[@charset]'):
        charset = html.Element('meta')
        charset.set('charset', 'utf-8')
        head.insert(0, charset)

    return _serialize_document(root)


def extract_paragraphs(processing_dom: html.HtmlElement, uid_map: dict[str, html.HtmlElement],
                       include_parents: bool = True) -> list[dict[str, str]]:
    """Extract paragraphs.

    The content_type field identifies the paragraph content type:

        'block_element': a standalone block-level element

        'inline_elements': a sequence containing only inline elements

        'unwrapped_text': plain text without an enclosing element

        'mixed': mixed content containing text and inline elements

    :param processing_dom:
    :param uid_map:
    :param include_parents:
    :return: Paragraph dictionaries with html, content_type, and _original_element
    """

    # Map each table to its data-table or layout-table classification.
    table_types = {}

    # Classify all tables first.
    for table in processing_dom.xpath('.//table'):
        table_types[table.get('data-uid')] = is_data_table(table)

    # Map each list to its content-list or layout-list classification.
    list_types = {}

    def is_block_element(node) -> bool:
        """Determine whether a node is a block-level element."""
        def judge_special_case(node, expected_tags, types_map):
            ancestor = node
            while ancestor is not None and ancestor.tag not in expected_tags:
                ancestor = ancestor.getparent()

            if ancestor is not None:
                ancestor_uid = ancestor.get('data-uid')
                if types_map.get(ancestor_uid, False):
                    # Children of data tables or content lists are not block elements.
                    return False
                else:
                    # Children of layout tables or lists are block elements.
                    return True

        # Handle table and list special cases.
        if node.tag in ('td', 'th'):
            return judge_special_case(node, ['table'], table_types)

        if node.tag == 'li':
            return judge_special_case(node, ['ul', 'ol'], list_types)

        if node.tag == 'dt' or node.tag == 'dd':
            return judge_special_case(node, ['dl'], list_types)

        # Handle all other elements by default.
        if node.tag in NO_BLOCK_TAGS or node.tag in INLINE_TAGS:
            return False
        return isinstance(node, html.HtmlElement)

    def has_block_descendants(node):
        if node.tag in NO_BLOCK_TAGS:
            return False
        for child in node.iterdescendants():
            parent = child.getparent()
            if parent is not None and (parent.tag in NO_BLOCK_TAGS or parent.get('cc-no-block') == 'true'):
                child.set('cc-no-block', 'true')
            if child.get('cc-no-block') != 'true' and is_block_element(child):
                if node.tag in INLINE_TAGS:
                    original_element = uid_map.get(node.get('data-uid'))
                    original_element.set('cc-block-type', 'true')
                return True
        return False

    def is_content_list(list_element):
        # Get list items across supported list types.
        items = list_element.xpath('li | dt | dd')

        # A list without list items is not a content list.
        if len(items) == 0:
            return False
        # Lists with non-list-item children are layout lists.
        if has_non_listitem_children(list_element):
            return False

        # Lists with block-level descendants in any item are layout lists.
        for item in items:
            if has_block_descendants(item):
                return False

        # Classify all remaining lists as content lists.
        return True

    # Classify all lists first.
    for list_element in processing_dom.xpath('.//ul | .//ol | .//dl'):
        list_types[list_element.get('data-uid')] = is_content_list(list_element)

    def clone_structure(path: list[html.HtmlElement]) -> tuple[html.HtmlElement, html.HtmlElement]:
        """Clone a node's structure."""
        if not path:
            raise ValueError('Path cannot be empty')
        if not include_parents:
            last_node = html.Element(path[-1].tag)
            last_node.attrib.update(path[-1].attrib)
            _project_nearest_ancestor_link(last_node, path)
            return last_node, last_node

        root = html.Element(path[0].tag)
        root.attrib.update(path[0].attrib)
        current = root
        for node in path[1:-1]:
            new_node = html.Element(node.tag)
            new_node.attrib.update(node.attrib)
            current.append(new_node)
            current = new_node

        last_node = html.Element(path[-1].tag)
        last_node.attrib.update(path[-1].attrib)
        current.append(last_node)
        return root, last_node

    paragraphs = []

    def process_node(node: html.HtmlElement, path: list[html.HtmlElement]):
        """Recursively process a node."""
        current_path = path + [node]
        inline_content = []
        content_sources = []

        # Process the node's text.
        if node.text and node.text.strip():
            inline_content.append(('direct_text', node.text.strip()))
            content_sources.append('direct_text')

        # Process child nodes.
        for child in node:
            if is_block_element(child) or has_block_descendants(child):
                if child.tag == 'br':
                    inline_content.append(('element', child))
                    content_sources.append('element')
                # Process accumulated inline content.
                if inline_content:
                    try:
                        root, last_node = clone_structure(current_path)
                        merge_inline_content(last_node, inline_content)

                        content_type = 'mixed'
                        if all(t == 'direct_text' for t in content_sources):
                            content_type = 'unwrapped_text'
                        elif all(t == 'element' for t in content_sources):
                            content_type = 'inline_elements'

                        # Retrieve the original element.
                        original_element = uid_map.get(node.get('data-uid'))
                        paragraphs.append({
                            'html': etree.tostring(root, encoding='unicode').strip(),
                            'content_type': content_type,
                            '_original_element': original_element  # Add the original element reference
                        })
                    except ValueError:
                        pass
                    inline_content = []
                    content_sources = []
                if child.tag != 'br':
                    # Process block-level elements.
                    if table_types.get(child.get('data-uid')) or (not has_block_descendants(child)):
                        try:
                            root, last_node = clone_structure(current_path + [child])
                            last_node.text = child.text if child.text else None
                            for grandchild in child:
                                last_node.append(copy.deepcopy(grandchild))

                            # Retrieve the original element.
                            original_element = uid_map.get(child.get('data-uid'))
                            paragraphs.append({
                                'html': etree.tostring(root, encoding='unicode').strip(),
                                'content_type': 'block_element',
                                '_original_element': original_element  # Add the original element reference
                            })
                        except ValueError:
                            pass
                    else:
                        process_node(child, current_path)

                # Process tail text.
                if child.tail and child.tail.strip():
                    inline_content.append(('tail_text', child.tail.strip()))
                    content_sources.append('tail_text')
            else:
                inline_content.append(('element', child))
                content_sources.append('element')
                if child.tail and child.tail.strip():
                    inline_content.append(('tail_text', child.tail.strip()))
                    content_sources.append('tail_text')

        # Process remaining inline content.
        if inline_content:
            try:
                root, last_node = clone_structure(current_path)
                merge_inline_content(last_node, inline_content)

                content_type = 'mixed'
                if all(t == 'direct_text' for t in content_sources):
                    content_type = 'unwrapped_text'
                elif all(t == 'element' for t in content_sources):
                    content_type = 'inline_elements'
                elif all(t in ('direct_text', 'tail_text') for t in content_sources):
                    content_type = 'unwrapped_text'

                # Retrieve the original element.
                original_element = uid_map.get(node.get('data-uid'))
                paragraphs.append({
                    'html': etree.tostring(root, encoding='unicode').strip(),
                    'content_type': content_type,
                    '_original_element': original_element  # Add the original element reference
                })
            except ValueError:
                pass

    def merge_inline_content(parent: html.HtmlElement, content_list: list[tuple[str, str]]):
        """Merge inline content."""
        last_inserted = None
        for idx, (item_type, item) in enumerate(content_list):
            if item_type in ('direct_text', 'tail_text'):
                if last_inserted is None:
                    if not parent.text:
                        parent.text = item
                    else:
                        parent.text += ' ' + item
                else:
                    if last_inserted.tail is None:
                        last_inserted.tail = item
                    else:
                        last_inserted.tail += ' ' + item
            else:
                item_copy = copy.deepcopy(item)
                if idx == len(content_list) - 1 and item_copy.tag == 'br':
                    item_copy.tail = None
                parent.append(item_copy)
                last_inserted = item

    # Start processing.
    process_node(processing_dom, [])

    # Deduplicate paragraphs.
    seen = set()
    unique_paragraphs = []
    for p in paragraphs:
        if p['html'] not in seen:
            seen.add(p['html'])
            unique_paragraphs.append(p)

    return unique_paragraphs

def process_paragraphs(paragraphs: list[dict[str, str]], uid_map: dict[str, html.HtmlElement], cutoff_length: int = 500) -> tuple[str, html.HtmlElement]:
    """Process paragraphs and assign matching _item_id values in the original DOM.

    Args:
        paragraphs: Paragraph dictionaries containing html, content_type, and
            _original_element.
        uid_map: Mapping from data-uid values to elements in the original DOM.
        cutoff_length: Maximum text length for each paragraph.

    Returns:
        Simplified HTML with item IDs.
    """
    result = []
    item_id = 1

    for para in paragraphs:
        try:
            root = _parse_paragraph_fragment(para['html'])
            root_for_xpath = copy.deepcopy(root)
            root = _normalize_model_paragraph_root(root)
            content_type = para.get('content_type', 'block_element')

            # Shared processing steps
            clean_attributes(root)
            simplify_list(root)
            compress_data_tables_for_model(root)
            # remove_inline_tags(root)

            # Skip non-meaningful content
            if not is_meaningful_content(root):
                continue

            # Normal paragraphs are bounded as a whole. Structured lists keep
            # every representative entry and bound those entries independently.
            _truncate_model_paragraph(root, max_length=cutoff_length)

            # Assign the same _item_id to the current paragraph and original element
            current_id = str(item_id)
            root.set('_item_id', current_id)

            # Handle non-block elements (inline_elements, unwrapped_text, mixed)
            original_parent = para['_original_element']  # Parent of the direct children in the original page
            if content_type != 'block_element':
                if original_parent is not None:
                    # root_for_xpath has child elements
                    original_element = uid_map.get(root_for_xpath.get('data-uid'))
                    if len(root_for_xpath) > 0:
                        if root_for_xpath.tag in INLINE_TAGS and original_element.tag != 'body' and original_element.get('cc-block-type') != 'true':
                            original_element.set('_item_id', current_id)
                        else:
                            # Collect child elements to wrap
                            children_to_wrap = []
                            for child in root_for_xpath.iterchildren():
                                child_uid = child.get('data-uid')
                                if child_uid and child_uid in uid_map:
                                    original_child = uid_map[child_uid]
                                    children_to_wrap.append(original_child)

                            if children_to_wrap:
                                # Determine the wrapping range
                                first_child = children_to_wrap[0]
                                last_child = children_to_wrap[-1]

                                # Get positions within the parent
                                start_idx = original_parent.index(first_child)
                                end_idx = original_parent.index(last_child)

                                # Collect all nodes to move
                                nodes_to_wrap = []
                                for i in range(start_idx, end_idx + 1):
                                    nodes_to_wrap.append(original_parent[i])

                                # Handle leading text
                                leading_text = original_parent.text if start_idx == 0 else original_parent[
                                    start_idx - 1].tail

                                # Handle trailing text
                                trailing_text = last_child.tail

                                # Create the wrapper element
                                wrapper = etree.Element(TAIL_BLOCK_TAG)
                                wrapper.set('_item_id', current_id)
                                # Copy cc-select to the wrapper so it remains at the same level as _item_id
                                if original_parent.get('cc-select') is not None:
                                    wrapper.set('cc-select', original_parent.get('cc-select'))

                                # Set leading text
                                if leading_text:
                                    wrapper.text = leading_text
                                    if start_idx == 0:
                                        original_parent.text = None
                                    else:
                                        original_parent[start_idx - 1].tail = None

                                # Move nodes into the wrapper
                                for node in nodes_to_wrap:
                                    original_parent.remove(node)
                                    wrapper.append(node)

                                # Insert the wrapper
                                original_parent.insert(start_idx, wrapper)

                                # Set trailing text
                                if last_child.tag == 'br' and trailing_text:
                                    wrapper.tail = trailing_text
                                    last_child.tail = None
                    else:
                        if content_type == 'inline_elements':
                            original_element.set('_item_id', current_id)
                        else:
                            # root_for_xpath contains only text
                            if root_for_xpath.text and root_for_xpath.text.strip():
                                # 1. Find the matching text node in the original DOM
                                found = False

                                # Check the parent node's text
                                if original_parent.text and original_parent.text.strip() == root_for_xpath.text.strip():
                                    # Create the wrapper
                                    wrapper = etree.Element(TAIL_BLOCK_TAG)
                                    wrapper.set('_item_id', current_id)
                                    wrapper.text = original_parent.text
                                    # Copy cc-select from the parent to the wrapper
                                    if original_parent.get('cc-select') is not None:
                                        wrapper.set('cc-select', original_parent.get('cc-select'))
                                    # Replace the parent node's text
                                    original_parent.text = None

                                    # Insert the wrapper as the first child
                                    if len(original_parent) > 0:
                                        original_parent.insert(0, wrapper)
                                    else:
                                        original_parent.append(wrapper)

                                    found = True

                                # Check child nodes' tail text
                                if not found:
                                    for child in original_parent.iterchildren():
                                        if child.tail and child.tail.strip() == root_for_xpath.text.strip():
                                            # Create the wrapper
                                            wrapper = etree.Element(TAIL_BLOCK_TAG)
                                            wrapper.set('_item_id', current_id)
                                            wrapper.text = child.tail
                                            # Copy cc-select from the parent to the wrapper
                                            if original_parent.get('cc-select') is not None:
                                                wrapper.set('cc-select', original_parent.get('cc-select'))
                                            # Replace the tail text
                                            child.tail = None

                                            # Insert the wrapper after the child node
                                            parent = child.getparent()
                                            index = parent.index(child)
                                            parent.insert(index + 1, wrapper)

                                            break

            else:
                # Set the attribute directly on block-level elements
                original_parent.set('_item_id', current_id)
                for child in original_parent.iterdescendants():
                    if child.get('cc-select') is not None:
                        original_parent.set('cc-select', child.get('cc-select'))
                        break

            item_id += 1

            # Save the processing result
            cleaned_html = etree.tostring(root, method='html', encoding='unicode').strip()
            result.append({
                'html': cleaned_html,
                '_item_id': current_id,
                'content_type': content_type
            })

        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to process paragraph",
            )
            raise

    # Assemble the final HTML
    simplified_html = '<html><head><meta charset="utf-8"></head><body>' + ''.join(
        p['html'] for p in result) + '</body></html>'

    return post_process_html(simplified_html)

def html_to_element(html_str: str) -> html.HtmlElement:
    """
    Convert HTML string to element.

    Args:
        html_str: HTML string

    Returns:
        HTML element
    """

    return _parse_document(html_str)

# ---------------------------------------------------------------------------
# Public interface: simplify_html
# ---------------------------------------------------------------------------

def simplify_html(html_str: str, cutoff_length: int = 500, remain_head: bool=True) -> tuple[str, str]:
    """
    Simplify HTML into LLM-friendly fragments and return mapped original HTML.

    The mapped HTML retains the original structure and includes _item_id
    attributes for mapping model annotations back to that structure.

    :param html_str: Original HTML string
    :param cutoff_length: Maximum text length per paragraph; longer text is truncated with an ellipsis
    :return: (simplified_html, map_html)
       - simplified_html: Simplified HTML containing only paragraph-level content, with _item_id attributes
       - map_html: Original-structure HTML with _item_id attributes, used by extract_main_html for mapping
    """
    original_dom = _parse_document(html_str)
    
    _remove_tags(original_dom)
    _replace_specific_elements(original_dom)
    model_head = _build_model_head(original_dom, cutoff_length)

    _add_data_uids(original_dom)
    uid_map = _build_uid_map(original_dom)

    processing_dom = copy.deepcopy(original_dom)
    truncate_attributes(processing_dom)
    _remove_head_from_processing_dom(processing_dom)

    paragraphs = extract_paragraphs(processing_dom, uid_map, include_parents=False)
    simplified_str= process_paragraphs(paragraphs, uid_map, cutoff_length=cutoff_length)
    if remain_head:
        simplified_str = _inject_model_head(simplified_str, model_head)

    _remove_all_uids(original_dom)
    map_html = _serialize_document(original_dom)
    _validate_item_id_mapping(simplified_str, map_html)
    return simplified_str, map_html


def _validate_item_id_mapping(simplified_html: str, map_html: str) -> None:
    """Reject ambiguous or incomplete model-to-execution DOM mappings."""
    simplified_dom = _parse_document(simplified_html)
    mapped_dom = _parse_document(map_html)
    simplified_ids = simplified_dom.xpath(f'//*[@{ITEM_ID_ATTR}]/@{ITEM_ID_ATTR}')
    mapped_ids = mapped_dom.xpath(f'//*[@{ITEM_ID_ATTR}]/@{ITEM_ID_ATTR}')

    duplicate_simplified = [
        item_id
        for item_id, count in Counter(simplified_ids).items()
        if count > 1
    ]
    duplicate_mapped = [
        item_id
        for item_id, count in Counter(mapped_ids).items()
        if count > 1
    ]
    if duplicate_simplified:
        raise ValueError(
            f'Duplicate item IDs in simplified HTML: {duplicate_simplified[:10]}'
        )
    if duplicate_mapped:
        raise ValueError(
            f'Duplicate item IDs in mapped HTML: {duplicate_mapped[:10]}'
        )

    missing_from_mapped = sorted(set(simplified_ids) - set(mapped_ids))
    missing_from_simplified = sorted(set(mapped_ids) - set(simplified_ids))
    if missing_from_mapped or missing_from_simplified:
        raise ValueError(
            'Simplified and mapped HTML contain different item IDs: '
            f'missing_from_mapped={missing_from_mapped[:10]}, '
            f'missing_from_simplified={missing_from_simplified[:10]}'
        )

    head_ids = mapped_dom.xpath(f'//head//*[@{ITEM_ID_ATTR}]/@{ITEM_ID_ATTR}')
    if head_ids:
        raise ValueError(
            f'Mapped head unexpectedly contains item IDs: {head_ids[:10]}'
        )


def _remove_internal_annotations(root: html.HtmlElement) -> None:
    """Remove mapping wrappers and all temporary pipeline attributes."""
    for tail_block in list(root.xpath(f'//{TAIL_BLOCK_TAG}')):
        tail_block.drop_tag()
    for element in root.iter():
        for attribute in INTERNAL_ATTRIBUTES:
            element.attrib.pop(attribute, None)




# ---------------------------------------------------------------------------
# Public interface: extract_main_html (replace unselected content with "..." while preserving tags and structure)
# ---------------------------------------------------------------------------



def replace_recursive_by_condition(
    root: html.HtmlElement,
    replace_condition: Callable[[html.HtmlElement], bool],
    semantic_attribute_nodes: set[html.HtmlElement] | None = None,
    ) -> html.HtmlElement:
    """
    Recursively replace an element's inner content with "..." when a condition is met.
    
    Args:
        root: Root HTML element to process (from map_html)
        replace_condition: Predicate that triggers replacement when it returns True
        semantic_attribute_nodes: Nodes whose semantic attributes are preserved during replacement
    """
    semantic_attribute_nodes = semantic_attribute_nodes or set()

    if replace_condition(root):
        subtree_text = re.sub(
            r'\s+', ' ', ' '.join(root.itertext(with_tail=True))
        ).strip()

        # Remove the unselected subtree but preserve this node and its tail so
        # selected descendants elsewhere keep a stable DOM path.
        for child in list(root):
            root.remove(child)

        if root in semantic_attribute_nodes:
            kept_attributes = {
                name: value
                for name, value in root.attrib.items()
                if _is_semantic_attribute(name)
            }
            root.attrib.clear()
            root.attrib.update(kept_attributes)
        else:
            root.attrib.clear()

        if isinstance(root.tag, str) and root.tag.lower() not in VOID_TAGS:
            root.text = "..." if subtree_text else None
    else:
        for child in list(root.iterchildren()):
            replace_recursive_by_condition(
                child,
                replace_condition,
                semantic_attribute_nodes,
            )
            
    return root




def extract_main_html(map_html: str, response: dict[str, list[str]]) -> str:
    """
    Replace content outside the LLM's useful_item_ids list with "..." while
    preserving the original structure.

    :param map_html: Original-structure HTML with _item_id attributes
    :param response: Dictionary in the format {"useful_item_ids": ["id1", "id2", ...]}
    :return: Processed HTML string
    """
    root = html_to_element(map_html)
    _sanitize_head(root)
    
    # Extract the IDs to retain
    useful_ids = response.get("useful_item_ids", [])
    elements_to_remained = set()
    selected_elements = []
    direct_useful_siblings = set()
    short_text_siblings = set()

    # Head has already been reduced by deterministic rules. It is always
    # useful context and does not participate in useful-item ID selection.
    for head in root.xpath('//head'):
        elements_to_remained.update(head.iter())
        elements_to_remained.update(head.iterancestors())

    # 1. Find all seed nodes and their related nodes by ID
    for remained_id in useful_ids:
        # Locate elements by their item_id attribute
        elem_list = root.xpath(f'//*[@{ITEM_ID_ATTR}="{remained_id}"]')
        if not elem_list:
            continue
            
        elem = elem_list[0]
        selected_elements.append(elem)

        parent = elem.getparent()
        if parent is not None:
            for sibling in parent.iterchildren():
                if sibling is elem or not isinstance(sibling.tag, str):
                    continue
                direct_useful_siblings.add(sibling)
                sibling_text = re.sub(
                    r'\s+', ' ', ' '.join(sibling.itertext(with_tail=True))
                ).strip()
                if (
                    sibling.tag.lower() in SHORT_CONTEXT_SIBLING_TAGS
                    and sibling_text
                    and len(sibling_text) <= SHORT_CONTEXT_MAX_TEXT_LENGTH
                ):
                    short_text_siblings.add(sibling)
        
        # Preserve the node and all its descendants
        for child in elem.iter():
            elements_to_remained.add(child)
            
        # Preserve all ancestors to keep the DOM path intact
        for ancestor in elem.iterancestors():
            elements_to_remained.add(ancestor)

    for sibling in short_text_siblings:
        elements_to_remained.update(sibling.iter())

    # Empty, malformed, or stale model output must not erase the whole page.
    # Return the filtered mapped DOM conservatively so the caller can retry or
    # continue inference with all remaining content.
    if not selected_elements:
        _remove_internal_annotations(root)
        return _serialize_document(root)

    # 2. Recover omitted adjacent br tags to preserve line breaks
    last_element: html.HtmlElement | None = None
    for element in root.iter():
        if last_element is not None:
            # Retain br when the current element is br and the previous one is retained
            if element.tag == 'br' and (last_element in elements_to_remained and last_element.tag != 'br'):
                elements_to_remained.add(element)
            # Restore the previous br when the current element is retained
            if last_element.tag == 'br' and (element in elements_to_remained and element.tag != 'br'):
                elements_to_remained.add(last_element)
        last_element = element

    # 3. Recursively replace content of nodes outside the retained set with "..."
    # The lambda replaces nodes that are not in elements_to_remained
    replace_recursive_by_condition(
        root,
        lambda x: x not in elements_to_remained,
        direct_useful_siblings,
    )

    # 4. Post-process: remove temporary tags and item_id attributes
    _remove_internal_annotations(root)
    
    # 5. Use unified HTML serialization. The parser decodes entities when reading attributes.
    return _serialize_document(root)

# Compatibility: an interface retaining only the main subtree could be added for callers
# expecting the legacy "keep main, remove other" behavior; currently only replacement with "..." is implemented.

