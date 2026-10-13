from bs4 import BeautifulSoup
import re

from .models import Document


def ingest(name: str, content: str, format: str) -> Document:
    if format == "html":
        soup = BeautifulSoup(content, "html.parser")
        for node in soup(["script", "style", "nav", "footer", "noscript", "header"]):
            node.decompose()
        articles = soup.find_all("article")
        root = max(articles, key=lambda node: len(node.get_text()), default=None)
        root = root or soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
        for tag in root.find_all("br"):
            tag.replace_with("\n")
        for tag in root.find_all(["p", "h1", "h2", "h3", "h4", "li", "tr", "section", "div"]):
            tag.insert_after("\n")
        content = root.get_text(separator=" ")
        content = re.sub(r"[^\S\n]+", " ", content)
        content = "\n".join(line.strip() for line in content.splitlines() if line.strip())
    if not content.strip():
        raise ValueError("document contains no text")
    return Document(name=name, text=content)
