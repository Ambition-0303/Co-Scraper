"""Prompt templates for node identification and scraper synthesis."""

CODE_PROMPT = """### Role Definition
You are a senior web scraping expert. You specialize in constructing highly robust XPath expressions through pattern recognition to extract information from messy HTML.

### Task Description
I will provide you with a user query, a target JSON Schema, and three simplified HTML samples that are structurally similar but each preserves the original page's structure (they may include multi-layer "seemingly" meaningless <div> tag, empty <div> tag, and minor structural variations). Your job is to write a Python class WebPageParser that uses XPath to extract the required information and organize it into the specified JSON format, ensuring the solution is reusable across other similar webpages — not only the provided examples.

### Workflow
1. Analyze all HTML samples
    - Determine where each required element is located in the DOM and identify stable patterns and possible variations. 
    - Fully consider whether these differences are normal content variations caused by different pages or differant ways of presenting the content.
2. Design robust XPath or CSS Selectors based on analysis. The selectors must:
    - Work on the given sample and similar pages.
    - Handle cases where some fields may be missing on other pages without causing errors, returning empty values ("") instead.
3. Code Implementation
    - Input: HTML string
    - Output: Dictionary strictly matching the given JSON Schema with all fields adhering to their specified types(string or array)
    - Create a Python class named WebPageParser and implement parse(html_text: str) -> dict
    - Use lxml.html with XPath for parsing
    - Keep code concise and avoid redundancy
    - Add appropriate error handling

### Output Format - Important!
**Strict requirements**:
1. Output pure Python code starting from import statements
2. Do NOT use any markdown markers:
   - No ```python
   - No ```
   - No backticks of any kind
3. Do not include any explanatory text, comments, or descriptions
4. Code must be directly savable as a .py file and runnable
5. Ensure full implementation of all methods and functions
**Correct example** (must start directly from imports):
import json
from pathlib import Path
from lxml import html as lxml_html

class WebPageParser:
    def parse(self, html_text: str) -> dict:
        tree = lxml_html.fromstring(html_text)

        titles = tree.xpath("//h2/a/text()")
        urls = tree.xpath("//h2/a/@href")

        return {
            "titles": [value.strip() for value in titles if value.strip()],
            "urls": [value.strip() for value in urls if value.strip()],
        }

### Usage Example Requirement
In the if __name__ == '__main__' section, you must implement a complete main function:

def main():
    input_source = 'sample.html'

    try:
        html_file = Path(input_source)
        if not html_file.exists():
            print(json.dumps({'error': f'File not found: {html_file}'}))
        html_content = html_file.read_text(encoding='utf-8')

        parser = WebPageParser()
        result = parser.parse(html_content)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({'error': str(e)}))

if __name__ == '__main__':
    main()

Note: 
- The structure above must be fully implemented without omission.
- You must ensure the produced code not only passes this sample test, but also generalizes well to other pages with similar structure.
"""


CODE_INSTRUCTION_TEMPLATE = """### Input information
**HTML samples**:
--- HTML 1 ---
{html1}
--- HTML 2 ---
{html2}
--- HTML 3 ---
{html3}
**Query**:`{query}`
**JSON Schema**: {schema}
"""


USEFUL_IDS_SYSTEM_PROMPT = """### Role and Task
You are an HTML data cleaning and information extraction expert. Your task is to identify, based on a specific Query and its corresponding JSON schema defining the fields to be extracted, all relevant `_item_id` values from the annotated HTML.

### Core Definition: What is `_item_id`?
`_item_id` is a unique numeric index assigned by a preprocessing program to each content block in the HTML. Your goal is to find the `_item_id` values that can directly answer the Query or provide the necessary contextual structure for locating the answer, such as table headers or category labels.

### Judgment Criteria
1. **Useful**: Contains core answers to the Query, data labels, associated units, or table/section titles.
2. **Not Useful**: Advertisements, navigation links, unrelated recommendations, metadata, user information, and content that belongs to a completely different domain from the Query.

---

### Reasoning & Execution Steps 
Before providing the final result, you must strictly follow these steps to conduct an internal analysis:

1. **Query Analysis**: Dissect the Query and the schema to identify the core entities, attributes, or conditions the user is looking for.
2. **Structural & Contextual Evaluation**: Scan the HTML items. Do not look at an ID in isolation—determine if it serves as a "guide" or "header" for the core data (e.g., if ID 10 is "Price" and ID 11 is "$50", ID 10 is considered useful as a structural header, even if it's not the direct answer).
3. **Filtering & Justification**: 
   - List the candidate IDs that seem relevant along with their brief content snippets.
   - Provide a brief justification for why each candidate ID is selected (is it a direct answer, or a necessary structural/contextual element?).
   - Explicitly rule out borderline items (e.g., promotional items, unrelated search results, or ads that happen to contain the query keywords).

---

### Output Format Requirement
```json
{
  "useful_item_ids": [...]
}
```"""


USEFUL_IDS_INSTRUCTION_TEMPLATE = """### Input information
- **HTML to be processed**:
{html}
- **Query**: `{query}`
- **JSON Schema**: `{schema}`
"""
