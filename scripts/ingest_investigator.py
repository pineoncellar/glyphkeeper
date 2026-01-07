"""
调查员信息导入脚本
支持从 JSON 或 YAML 文件批量导入调查员信息到数据库
同时将背景故事存入 RAG（符合双脑架构）
"""
import asyncio
import sys
import json
import yaml
import argparse
from pathlib import Path
from typing import Dict, List, Optional

# 添加项目根目录到 python path
sys.path.append(str(Path(__file__).parent.parent))

from src.core import get_logger
from src.memory.database import db_manager
from src.memory.repositories.entity_repo import EntityRepository
from src.memory.RAG_engine import RAGEngine

logger = get_logger(__name__)


class InvestigatorImporter:
    """调查员导入器"""
    
    def __init__(self):
        self.stats_loaded = 0
        self.failed = 0
        self.skipped = 0
        self.rag_engine = None
    
    async def import_from_file(self, file_path: Path) -> bool:
        """从文件导入调查员数据"""
        try:
            # 根据文件扩展名选择解析器
            if file_path.suffix.lower() in ['.json']:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            elif file_path.suffix.lower() in ['.yaml', '.yml']:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
            else:
                logger.error(f"不支持的文件格式: {file_path.suffix}")
                return False
            
            # 支持两种格式：单个调查员或调查员列表
            if isinstance(data, dict):
                if 'investigators' in data:
                    investigators = data['investigators']
                else:
                    # 单个调查员
                    investigators = [data]
            elif isinstance(data, list):
                investigators = data
            else:
                logger.error("文件格式错误：应为字典或列表")
                return False
            
            logger.info(f"找到 {len(investigators)} 个调查员待导入")
            
            # 初始化 RAG 引擎（用于存储背景故事）
            try:
                self.rag_engine = await RAGEngine.get_instance(domain="world")
                logger.info("RAG 引擎初始化成功")
            except Exception as e:
                logger.warning(f"RAG 引擎初始化失败，将跳过背景故事导入: {e}")
            
            # 批量导入
            async with db_manager.session_factory() as session:
                entity_repo = EntityRepository(session)
                
                for idx, inv_data in enumerate(investigators, 1):
                    try:
                        await self._import_single_investigator(entity_repo, inv_data, idx)
                    except Exception as e:
                        logger.error(f"导入第 {idx} 个调查员失败: {e}")
                        self.failed += 1
            
            # 打印统计信息
            logger.info("=" * 60)
            logger.info(f"导入完成！")
            logger.info(f"  成功: {self.stats_loaded}")
            logger.info(f"  跳过: {self.skipped}")
            logger.info(f"  失败: {self.failed}")
            logger.info("=" * 60)
            
            return self.failed == 0
            
        except Exception as e:
            logger.error(f"导入过程出错: {e}")
            return False
    
    async def _import_single_investigator(
        self, 
        entity_repo: EntityRepository, 
        data: Dict, 
        idx: int
    ):
        """导入单个调查员"""
        # 必填字段
        name = data.get('name')
        if not name:
            logger.error(f"第 {idx} 个调查员缺少 'name' 字段，跳过")
            self.skipped += 1
            return
        
        # 检查是否已存在（通过 key 或 name）
        key = data.get('key')
        existing = None
        if key:
            existing = await entity_repo.get_by_key(key)
        if not existing:
            existing = await entity_repo.get_by_name(name)
        
        if existing:
            logger.warning(f"调查员 '{name}' 已存在，跳过")
            self.skipped += 1
            return
        
        # 构建 Entity 基础信息
        tags = data.get('tags', [])
        # 确保有 "investigator" 标签
        if "investigator" not in tags:
            tags.append("investigator")
        
        stats = data.get('stats', {})
        attacks = data.get('attacks', [])
        location_key = data.get('location')
        
        # 如果指定了位置，尝试查找
        location_id = None
        if location_key:
            from src.memory.repositories.location_repo import LocationRepository
            location_repo = LocationRepository(entity_repo.session)
            location = await location_repo.get_by_key(location_key)
            if location:
                location_id = location.id
            else:
                logger.warning(f"找不到位置 '{location_key}'，调查员将不设置初始位置")
        
        # 构建 InvestigatorProfile 信息
        profile_data = {}
        
        # 基础信息
        if 'player_name' in data:
            profile_data['player_name'] = data['player_name']
        if 'occupation' in data:
            profile_data['occupation'] = data['occupation']
        if 'age' in data:
            profile_data['age'] = data['age']
        if 'gender' in data:
            profile_data['gender'] = data['gender']
        if 'residence' in data:
            profile_data['residence'] = data['residence']
        if 'birthplace' in data:
            profile_data['birthplace'] = data['birthplace']
        
        # 背景故事（支持字典或字符串）
        backstory = data.get('backstory', {})
        if isinstance(backstory, str):
            profile_data['backstory'] = {'description': backstory}
        elif isinstance(backstory, dict):
            profile_data['backstory'] = backstory
        else:
            profile_data['backstory'] = {}
        
        # 资产详情
        if 'assets_detail' in data:
            profile_data['assets_detail'] = data['assets_detail']
        
        # 创建调查员（Entity + InvestigatorProfile）
        try:
            entity = await entity_repo.create_with_profile(
                name=name,
                key=key,
                tags=tags,
                stats=stats,
                attacks=attacks,
                location_id=location_id,
                profile_data=profile_data if profile_data else None
            )
            logger.info(f"✓ 成功导入调查员到数据库: {name} (ID: {entity.id})")
            
            # 将背景故事插入到 RAG（右脑）
            await self._insert_backstory_to_rag(entity, data, profile_data)
            
            self.stats_loaded += 1
        except Exception as e:
            logger.error(f"创建调查员 '{name}' 失败: {e}")
            self.failed += 1
            raise
    
    async def _insert_backstory_to_rag(
        self, 
        entity, 
        raw_data: Dict,
        profile_data: Optional[Dict]
    ):
        """将调查员的背景故事插入到 RAG 中（右脑）"""
        if not self.rag_engine:
            logger.debug("RAG 引擎未初始化，跳过背景故事导入")
            return
        
        # 构建背景故事文本
        backstory_parts = []
        
        # ===== 元数据标签 =====
        backstory_parts.append(f"[Investigator: {entity.name}]")
        if profile_data and profile_data.get('player_name'):
            backstory_parts.append(f"[Player: {profile_data['player_name']}]")
        if profile_data and profile_data.get('occupation'):
            backstory_parts.append(f"[Occupation: {profile_data['occupation']}]")
        
        # 添加标签以增强检索
        if entity.tags:
            backstory_parts.append(f"[Tags: {', '.join(entity.tags)}]")
        
        backstory_parts.append("")  # 空行分隔
        
        # ===== 基本信息 =====
        basic_info = []
        if profile_data:
            age = profile_data.get('age', raw_data.get('age'))
            gender = profile_data.get('gender', raw_data.get('gender'))
            residence = profile_data.get('residence', raw_data.get('residence'))
            birthplace = profile_data.get('birthplace', raw_data.get('birthplace'))
            
            if age:
                basic_info.append(f"年龄: {age}")
            if gender:
                basic_info.append(f"性别: {gender}")
            if residence:
                basic_info.append(f"居住地: {residence}")
            if birthplace:
                basic_info.append(f"出生地: {birthplace}")
        
        if basic_info:
            backstory_parts.append("基本信息:")
            backstory_parts.extend([f"  {info}" for info in basic_info])
            backstory_parts.append("")
        
        # ===== 背景故事内容 =====
        backstory = raw_data.get('backstory', {})
        has_backstory = False
        
        if isinstance(backstory, str):
            # 简单字符串形式
            if backstory.strip():
                backstory_parts.append("背景故事:")
                backstory_parts.append(backstory)
                backstory_parts.append("")
                has_backstory = True
                
        elif isinstance(backstory, dict):
            # 详细字典形式
            story_sections = [
                ('description', '人物描述'),
                ('ideology_beliefs', '思想与信念'),
                ('significant_people', '重要之人'),
                ('meaningful_location', '意义非凡之地'),
                ('treasured_possession', '宝贵之物'),
                ('traits', '性格特质'),
                ('injuries_scars', '伤口与疤痕'),
                ('phobias_manias', '恐惧症与躁狂症')
            ]
            
            for key, label in story_sections:
                if backstory.get(key):
                    backstory_parts.append(f"{label}:")
                    backstory_parts.append(f"  {backstory[key]}")
                    backstory_parts.append("")
                    has_backstory = True
        
        # ===== 资产信息 =====
        if profile_data and profile_data.get('assets_detail'):
            backstory_parts.append("资产状况:")
            backstory_parts.append(f"  {profile_data['assets_detail']}")
            backstory_parts.append("")
        
        # ===== 数据库关联信息 =====
        backstory_parts.append("---")
        backstory_parts.append(f"DB_UUID: {str(entity.id)}")
        if entity.key:
            backstory_parts.append(f"DB_KEY: {entity.key}")
        
        # 组合完整文本
        rag_text = "\n".join(backstory_parts)
        
        # 只有在有实质性内容时才插入
        if has_backstory or len(basic_info) > 2:
            try:
                await self.rag_engine.insert(rag_text)
                # 刷新数据库连接，避免超时
                from sqlalchemy import select
                async with db_manager.session_factory() as session:
                    await session.execute(select(1))
                logger.info(f"  └─ 背景故事已插入 RAG（右脑）")
            except Exception as e:
                logger.warning(f"  └─ 背景故事插入 RAG 失败: {e}")
        else:
            logger.debug(f"  └─ 无足够的背景故事内容，跳过 RAG 插入")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="导入调查员信息到数据库（双脑架构）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法：
  # 从 JSON 文件导入
  python import_investigator.py --file my_investigator.json
  
  # 从 YAML 文件导入
  python import_investigator.py --file team.yaml
  
  # 使用简短参数
  python import_investigator.py -f team.yaml

双脑架构说明：
  - 左脑（PostgreSQL）：存储数值属性、技能等结构化数据
  - 右脑（LightRAG）：存储背景故事、人物描述等非结构化文本
  
文件格式示例请参考 data/investigators/example_investigator.json
        """
    )
    parser.add_argument(
        "-f", "--file",
        type=str,
        required=True,
        help="调查员数据文件路径（支持 JSON 或 YAML 格式）"
    )
    
    args = parser.parse_args()
    
    file_path = Path("data/investigators") / args.file
    if not file_path.exists():
        logger.error(f"文件不存在: {file_path}")
        sys.exit(1)
    
    logger.info(f"准备从文件导入调查员: {file_path}")
    logger.info("=" * 60)
    
    importer = InvestigatorImporter()
    success = await importer.import_from_file(file_path)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
