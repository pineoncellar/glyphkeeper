#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试 CQRS 架构的新组件：StaticReadStore, SessionKnowledgeState, StateProjector, Archivist
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_module_imports():
    """测试所有新模块可导入"""
    from src.state.read_models import StaticReadStore
    from src.state.session_state import SessionKnowledgeState
    from src.state.projector import StateProjector
    from src.tools.archivist import Archivist
    print('[OK] 所有新模块导入成功')


def test_build_knowledge_registry():
    """测试 _build_knowledge_registry 逻辑"""
    from src.tools.ingestion import ModuleIngestor

    sample_data = {
        'global_knowledge': [
            {'key': 'fact_test', 'rag_content': '测试知识', 'tags_granted': ['tag_a']}
        ],
        'locations': [
            {
                'key': 'loc_test',
                'name': '测试场景',
                'base_desc': '描述',
                'tags': [],
                'exits': {},
                'entities': [
                    {
                        'key': 'npc_test',
                        'name': '测试NPC',
                        'dialogue_clues': [
                            {
                                'trigger': 'talk',
                                'target_knowledge': 'fact_test',
                                'required_check': None,
                                'flavor_text': '线索文本',
                            }
                        ],
                    }
                ],
                'interactables': [
                    {
                        'key': 'item_test',
                        'name': '测试物品',
                        'state': 'default',
                        'tags': [],
                        'clues': [
                            {
                                'trigger': 'search',
                                'target_knowledge': 'fact_test',
                                'required_check': {
                                    'skill': 'Spot Hidden',
                                    'difficulty': 'Regular',
                                },
                                'flavor_text': '你发现了线索',
                            }
                        ],
                    }
                ],
            }
        ],
    }

    registry = ModuleIngestor._build_knowledge_registry(sample_data)
    assert len(registry) == 1, f"预期 1 条知识，实际 {len(registry)}"
    assert registry[0]['knowledge_id'] == 'fact_test'
    assert 'tag_a' in registry[0]['tags_granted']
    print(f'[OK] _build_knowledge_registry: {len(registry)} 条知识')
    for r in registry:
        print(f'     - {r["knowledge_id"]}: id={r["id"][:8]}...')


def test_archivist_threshold():
    """测试 Archivist 阈值计算"""
    from src.tools.archivist import Archivist
    from src.domain.coc_rules import Difficulty

    archivist = Archivist()
    t_r = archivist._get_threshold_for_difficulty(Difficulty.REGULAR)
    t_h = archivist._get_threshold_for_difficulty(Difficulty.HARD)
    t_e = archivist._get_threshold_for_difficulty(Difficulty.EXTREME)
    assert t_r == 50, f"Regular 预期 50，实际 {t_r}"
    assert t_h == 25, f"Hard 预期 25，实际 {t_h}"
    assert t_e == 10, f"Extreme 预期 10，实际 {t_e}"
    print(f'[OK] Archivist 阈值: Regular={t_r}, Hard={t_h}, Extreme={t_e}')


def test_find_knowledge_id():
    """测试 _find_knowledge_id 查找逻辑"""
    from src.state.projector import StateProjector

    registry = [
        {'id': 'uuid-1', 'knowledge_id': 'fact_a'},
        {'id': 'uuid-2', 'knowledge_id': 'fact_b'},
    ]
    proj = StateProjector()
    result = proj._find_knowledge_id(registry, 'fact_a')
    assert result == 'uuid-1', f"预期 uuid-1，实际 {result}"
    result = proj._find_knowledge_id(registry, 'fact_b')
    assert result == 'uuid-2', f"预期 uuid-2，实际 {result}"
    result = proj._find_knowledge_id(registry, 'nonexistent')
    assert result is None, f"预期 None，实际 {result}"
    print(f'[OK] _find_knowledge_id: 查找逻辑正确')


def test_determine_success_level():
    """测试 CoC 检定判定"""
    from src.domain.coc_rules import determine_success_level, SuccessLevel

    # Regular success: roll <= skill
    assert determine_success_level(50, 40) == SuccessLevel.REGULAR
    # Hard success: roll <= skill/2
    assert determine_success_level(50, 25) == SuccessLevel.HARD
    # Extreme success: roll <= skill/5
    assert determine_success_level(50, 10) == SuccessLevel.EXTREME
    # Critical: roll == 1
    assert determine_success_level(50, 1) == SuccessLevel.CRITICAL
    # Failure
    assert determine_success_level(50, 60) == SuccessLevel.FAILURE
    print('[OK] determine_success_level: 判定逻辑正确')


def test_ingestion_docstring_updated():
    """验证 ingestion.py 文档字符串已更新"""
    import inspect
    from src.tools.ingestion import ModuleIngestor

    doc = inspect.getdoc(ModuleIngestor.ingest)
    assert doc is not None
    print(f'[OK] ingest() 文档字符串存在，长度: {len(doc)}')


if __name__ == '__main__':
    print('=' * 50)
    print('CQRS 组件单元测试')
    print('=' * 50)

    test_module_imports()
    test_build_knowledge_registry()
    test_archivist_threshold()
    test_find_knowledge_id()
    test_determine_success_level()
    test_ingestion_docstring_updated()

    print()
    print('=' * 50)
    print('所有测试通过!')
    print('=' * 50)
