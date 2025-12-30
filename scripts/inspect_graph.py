import networkx as nx
import matplotlib.pyplot as plt
import os
import sys

# 设置中文字体 (尝试常见的 Windows 中文字体)
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun']
plt.rcParams['axes.unicode_minus'] = False

def inspect_graphml(file_path):
    print(f"=== 正在检查图谱文件: {file_path} ===\n")
    
    if not os.path.exists(file_path):
        print(f"错误: 文件不存在 - {file_path}")
        return

    try:
        # 加载 GraphML 文件
        G = nx.read_graphml(file_path)
        
        # 1. 基本统计
        print(f"--- 基本统计 ---")
        print(f"节点数量: {G.number_of_nodes()}")
        print(f"边数量:   {G.number_of_edges()}")
        
        # 2. 节点预览
        print(f"\n--- 节点预览 (前 10 个) ---")
        nodes = list(G.nodes(data=True))
        for i, (node_id, data) in enumerate(nodes[:10]):
            # 尝试获取描述或标签
            desc = data.get('description', '无描述')
            # 截断过长的描述
            if len(desc) > 50:
                desc = desc[:47] + "..."
            print(f"[{i+1}] ID: {node_id}")
            print(f"    描述: {desc}")
            
        # 3. 关系预览
        print(f"\n--- 关系预览 (前 10 条) ---")
        edges = list(G.edges(data=True))
        for i, (source, target, data) in enumerate(edges[:10]):
            keywords = data.get('keywords', '无关键词')
            weight = data.get('weight', 1.0)
            print(f"[{i+1}] {source} --({keywords}, w={weight})--> {target}")

        # 4. 简单的可视化保存 (可选)
        print(f"\n--- 正在生成可视化预览图 (graph_preview.png) ---")
        plt.figure(figsize=(12, 12))
        
        # 使用 spring layout 布局
        pos = nx.spring_layout(G, k=0.5, iterations=50)
        
        # 绘制节点
        nx.draw_networkx_nodes(G, pos, node_size=500, node_color='lightblue', alpha=0.8)
        
        # 绘制边
        nx.draw_networkx_edges(G, pos, width=1.0, alpha=0.5)
        
        # 绘制标签 (只显示 ID)
        nx.draw_networkx_labels(G, pos, font_size=8, font_family='Microsoft YaHei')
        
        plt.title("LightRAG 知识图谱预览")
        plt.axis('off')
        
        output_img = "scripts/graph_preview.png"
        plt.savefig(output_img, format="PNG", dpi=300)
        print(f"可视化图已保存至: {os.path.abspath(output_img)}")
        
    except Exception as e:
        print(f"读取或解析 GraphML 失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 默认路径
    graph_path = os.path.join(os.path.dirname(__file__), "..", "data", "worlds", "book", "graph_chunk_entity_relation.graphml")
    inspect_graphml(os.path.abspath(graph_path))
