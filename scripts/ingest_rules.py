"""
导入 COC7th 规则数据到独立的规则知识库
使用独立的 coc7th_rules schema 和 LightRAG 实例
支持 JSON 格式和常规文档格式
"""
import asyncio
import sys
import json
from pathlib import Path

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.memory import get_rule_service
from src.core import get_logger

logger = get_logger(__name__)


def format_rule_entry(rule: dict) -> str:
    """
    将 JSON 规则条目格式化为文本
    
    Args:
        rule: 规则字典，包含 id, category, title, content, keywords
        
    Returns:
        格式化的规则文本
    """
    formatted = f"""# {rule.get('title', '未命名规则')}

**规则ID**: {rule.get('id', 'unknown')}
**分类**: {rule.get('category', 'general')}
**关键词**: {', '.join(rule.get('keywords', []))}

---

{rule.get('content', '')}

"""
    return formatted


async def ingest_json_rules(file_path: str):
    """
    导入 JSON 格式的规则文件
    
    Args:
        file_path: JSON 规则文件路径
    """
    logger.info(f"开始导入 JSON 规则文件: {file_path}")
    
    try:
        # 1. 读取 JSON 文件
        with open(file_path, 'r', encoding='utf-8') as f:
            rules = json.load(f)
        
        if not isinstance(rules, list):
            logger.error("JSON 文件格式错误：应为规则数组")
            return
        
        logger.info(f"✓ 成功加载 {len(rules)} 条规则")
        
        # 2. 格式化规则文本
        formatted_rules = []
        for i, rule in enumerate(rules, 1):
            if not isinstance(rule, dict):
                logger.warning(f"跳过无效规则条目 #{i}: 不是字典格式")
                continue
            
            try:
                formatted = format_rule_entry(rule)
                formatted_rules.append(formatted)
                logger.debug(f"✓ 格式化规则 #{i}: {rule.get('id', 'unknown')}")
            except Exception as e:
                logger.warning(f"格式化规则 #{i} 失败: {e}")
        
        if not formatted_rules:
            logger.error("没有有效的规则可导入")
            return
        
        logger.info(f"✓ 成功格式化 {len(formatted_rules)} 条规则")
        
        # 3. 批量插入到规则知识库
        rule_service = get_rule_service()
        logger.info("开始批量插入规则到知识库...")
        
        success_count = await rule_service.insert_batch(formatted_rules)
        
        logger.info(f"✓✓✓ 规则导入完成: {success_count}/{len(formatted_rules)} 条成功")
        
    except FileNotFoundError:
        logger.error(f"文件不存在: {file_path}")
    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析失败: {e}")
    except Exception as e:
        logger.error(f"导入 JSON 规则失败: {e}")
        raise

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="导入 COC7th 规则数据")
    parser.add_argument(
        "--dir", 
        type=str, 
        default="data/rules/rules.json",
        help="规则文档目录 (默认: data/rules/rules.json)"
    )
    
    args = parser.parse_args()
    
    print("""
    ╔════════════════════════════════════════════════════════════╗
    ║              COC7th 规则数据导入工具                       ║
    ╠════════════════════════════════════════════════════════════╣
    ║  使用独立的 coc7th_rules schema                           ║
    ║  与世界数据完全隔离                                        ║
    ║  支持格式: PDF, TXT, MD, DOCX, JSON                       ║
    ╚════════════════════════════════════════════════════════════╝
    """)

    rule_dir = args.dir
    print(f"📂 规则文档路径: {rule_dir}")

    asyncio.run(ingest_json_rules(rule_dir))
    
    print("\n✅ 导入完成！现在可以使用 get_rule_service() 查询规则数据。")
