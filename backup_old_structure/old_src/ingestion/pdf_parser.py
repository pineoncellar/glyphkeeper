"""
PDF Parser - PDF 文件解析器

功能：
- 从 PDF 文件中提取文本内容
- 支持多种 PDF 解析引擎（按优先级）
- 处理排版和格式问题

解析引擎选择：
1. LlamaParse (CloudAPI) - 推荐，高质量结构化解析
   - 保留排版、表格、多列布局
   - 适合复杂 TRPG 模组 PDF
   
2. PyMuPDF (fitz) - 本地方案，速度快
   - 适合简单文本提取
   - 支持图片、注释提取
   
3. pdfplumber - 表格提取专用
   - 适合属性表、数据表格
   
4. textract - 后备方案
   - 支持多种文件格式

核心函数：
- extract_text_from_pdf(file_path) -> str
  提取完整文本
  
- extract_pages_from_pdf(file_path, page_range) -> list[str]
  按页提取
  
- extract_images_from_pdf(file_path) -> list[bytes]
  提取图片（如地图、角色卡）
  
- parse_structured_pdf(file_path) -> dict
  结构化解析（标题、段落、表格）

用法示例：
    from .pdf_parser import extract_text_from_pdf
    
    text = await extract_text_from_pdf("module.pdf")
    structured = await parse_structured_pdf("module.pdf")
"""
