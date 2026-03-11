import os

def read_markdown(filepath: str) -> str:
    """Read the content of a markdown file."""
    if not os.path.exists(filepath):
        return ""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

def write_markdown(filepath: str, content: str) -> None:
    """Overwrite the file with new markdown content."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

def append_markdown(filepath: str, content: str) -> None:
    """Append content to an existing markdown file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(f"\n{content}")
