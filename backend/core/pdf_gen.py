import os

def generate_pdf_file(markdown_content: str, thread_id: str):
    """
    将 Markdown 写入文件。
    如果要转 PDF，建议使用 pdfkit 或 reportlab 配合中文字体文件。
    这里为了保证无需配置即可运行，我们生成一个 .md 文件供下载。
    """
    if not os.path.exists("static"):
        os.makedirs("static")
    
    filename = f"travel_plan_{thread_id}.md"
    filepath = os.path.join("static", filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    return filename