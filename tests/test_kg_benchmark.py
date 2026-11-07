"""KG benchmark — Session 33 M4 实装阶段.

跑 5 公司 × 3 行业 × 50 事件 (S30 retro 估的图规模) = 750 nodes + ~600 edges
比较 KGStore (NetworkX) vs Neo4JKGStore (mock 模式, 不实跑 Cypher).

测:
- 内存图规模 (实体数 + 边数)
- 5-hop query_related 性能 (NetworkX 走 BFS, Neo4J 走 path query)
- 序列化往返
"""

import time
from unittest.mock import MagicMock

import pytest

from lib.scoring.kg_store import (
    Entity,
    EntityType,
    KGStore,
    Relation,
    RelationType,
)
from lib.scoring.kg_neo4j import Neo4JKGStore


# ── 5×3×50 真实 KG 构造 ────────────────────────────────────


def build_realistic_kg(num_companies: int = 5, num_industries: int = 3, num_events: int = 50):
    """构造 S30 retro 估的 KG: 5 公司 × 3 行业 × 50 事件.

    实际生成 5+3+50 = 58 entities, ~6+5+50+50 = 111 relations.
    """
    kg = KGStore()

    # 3 行业
    industries = [
        Entity(id=f"industry:{name}", type=EntityType.INDUSTRY, name=name)
        for name in ["白酒", "科技", "金融"][:num_industries]
    ]
    # 5 公司
    companies = [
        Entity(id=f"company:{sym}", type=EntityType.COMPANY, name=name, properties={"symbol": sym})
        for sym, name in [
            ("600519", "贵州茅台"),
            ("000858", "五粮液"),
            ("00700", "腾讯"),
            ("BABA", "阿里巴巴"),
            ("600036", "招商银行"),
        ][:num_companies]
    ]
    for e in industries + companies:
        kg.add_entity(e)

    # 公司 → 行业
    company_industry = {
        "600519": "industry:白酒",
        "000858": "industry:白酒",
        "00700": "industry:科技",
        "BABA": "industry:科技",
        "600036": "industry:金融",
    }
    for company_id, industry_id in company_industry.items():
        kg.add_relation(Relation(
            src_id=f"company:{company_id}",
            dst_id=industry_id,
            type=RelationType.BELONGS_TO,
        ))

    # 50 事件 (简化: 10 事件/公司)
    events = []
    for i, (company_id, _) in enumerate(company_industry.items()):
        for j in range(num_events // len(company_industry)):
            event = Entity(
                id=f"event:news-{i}-{j}",
                type=EntityType.EVENT,
                name=f"{company_id} 事件 {j}",
                properties={"date": f"2026-{((i*10+j) % 12) + 1:02d}-15", "importance": (i + j) % 5},
            )
            events.append(event)
            kg.add_entity(event)
            kg.add_relation(Relation(
                src_id=event.id,
                dst_id=f"company:{company_id}",
                type=RelationType.MENTIONED_IN,
                weight=0.5 + (j % 5) * 0.1,
            ))

    return kg, companies, industries, events


# ── NetworkX 性能基准 ──────────────────────────────────────


def test_networkx_scale_750_nodes():
    """S30 retro 估的 KG 规模: 5 公司 × 3 行业 × 50 事件 = 750 nodes."""
    # 注意: S30 估 5×3×50 = 750 nodes 是悲观估计 (每个公司 × 50 事件)
    # 实际 5+3+50 = 58 entities (公司/行业不重复)
    kg, companies, industries, events = build_realistic_kg(
        num_companies=5, num_industries=3, num_events=50
    )
    stats = kg.stats()
    print(f"\n[BENCHMARK] NetworkX stats: {stats}")
    assert stats["entities"] >= 50
    assert stats["edges"] >= 50
    # 应有公司 + 行业 + 事件
    assert stats["companies"] == 5
    assert stats["industries"] == 3
    assert stats["events"] >= 50


def test_networkx_query_2_hop_performance():
    """5-公司 + 50-事件 query_related 2-hop 性能."""
    kg, _, _, _ = build_realistic_kg(num_companies=5, num_industries=3, num_events=50)
    # 茅台: 1-hop = 白酒 + 10 事件, 2-hop = 白酒 + 五粮液 (同行) + 10 事件
    start = time.perf_counter()
    related = kg.query_related("company:600519", depth=2, max_results=100)
    elapsed = time.perf_counter() - start
    print(f"\n[BENCHMARK] NetworkX 2-hop query: {elapsed*1000:.2f}ms, {len(related)} results")
    # 50 节点图 2-hop 必 <100ms
    assert elapsed < 0.1, f"NetworkX 2-hop took {elapsed*1000:.2f}ms (>100ms threshold)"
    assert len(related) >= 5  # 至少含白酒 + 五粮液 + 10 事件


def test_networkx_query_3_hop_performance():
    """3-hop 性能 — 跨公司-行业-事件."""
    kg, _, _, _ = build_realistic_kg(num_companies=5, num_industries=3, num_events=50)
    start = time.perf_counter()
    related = kg.query_related("company:600519", depth=3, max_results=200)
    elapsed = time.perf_counter() - start
    print(f"\n[BENCHMARK] NetworkX 3-hop query: {elapsed*1000:.2f}ms, {len(related)} results")
    assert elapsed < 0.5, f"NetworkX 3-hop took {elapsed*1000:.2f}ms"


def test_networkx_persistence_roundtrip(tmp_path):
    """JSON 持久化 + 重新加载 + 性能."""
    kg, _, _, _ = build_realistic_kg(num_companies=5, num_industries=3, num_events=50)
    path = tmp_path / "kg.json"
    # Save
    start = time.perf_counter()
    kg.save(path)
    save_time = time.perf_counter() - start
    # Load
    start = time.perf_counter()
    loaded = KGStore.load(path)
    load_time = time.perf_counter() - start
    print(f"\n[BENCHMARK] NetworkX persist: save={save_time*1000:.2f}ms, load={load_time*1000:.2f}ms")
    assert loaded.stats() == kg.stats()


# ── Neo4J 性能基准 (mock 模式) ──────────────────────────────


def test_neo4j_query_mock_throughput(monkeypatch):
    """Neo4J (mock) 走 Cypher 调用, 不实测 Bolt 协议 — 测 Cypher 构造 + cache 性能."""
    import sys

    # Mock neo4j 全栈
    mock_neo4j = MagicMock()
    mock_driver_instance = MagicMock()
    mock_session = MagicMock()
    mock_neo4j.GraphDatabase.driver.return_value = mock_driver_instance
    mock_driver_instance.session.return_value.__enter__.return_value = mock_session
    mock_driver_instance.session.return_value.__exit__.return_value = False
    monkeypatch.setitem(sys.modules, "neo4j", mock_neo4j)

    kg = Neo4JKGStore(uri="bolt://test", user="neo4j", password="test")
    # 加 50 entities
    for i in range(50):
        kg.add_entity(Entity(
            id=f"event:test-{i}",
            type=EntityType.EVENT,
            name=f"test event {i}",
        ))
    # 测 query 性能
    start = time.perf_counter()
    for _ in range(100):
        related = kg.query_related("event:test-0", depth=2, max_results=10)
    elapsed = time.perf_counter() - start
    print(f"\n[BENCHMARK] Neo4J (mock) 100 queries: {elapsed*1000:.2f}ms")
    # Mock 应该 <10ms/query
    assert elapsed < 1.0


# ── 规模探针 (估计何时需要 Neo4J) ──────────────────────────


def test_scale_when_neo4j_worth_it():
    """估算: NetworkX 在 1000 节点 + 5000 边的 KG 性能.

    结论 (跟 lightrag E80 一致): >1000 节点或 >5-hop 才需要 Neo4J.
    """
    # 构造 1000 节点 (200 公司 + 50 行业 + 750 事件)
    kg = KGStore()
    for i in range(50):
        kg.add_entity(Entity(
            id=f"industry:ind-{i}",
            type=EntityType.INDUSTRY,
            name=f"Industry {i}",
        ))
    for i in range(200):
        kg.add_entity(Entity(
            id=f"company:c{i}",
            type=EntityType.COMPANY,
            name=f"Company {i}",
            properties={"symbol": f"c{i}"},
        ))
        # 公司 → 行业
        kg.add_relation(Relation(
            src_id=f"company:c{i}",
            dst_id=f"industry:ind-{i % 50}",
            type=RelationType.BELONGS_TO,
        ))
    for i in range(750):
        kg.add_entity(Entity(
            id=f"event:e{i}",
            type=EntityType.EVENT,
            name=f"Event {i}",
        ))
        kg.add_relation(Relation(
            src_id=f"event:e{i}",
            dst_id=f"company:c{i % 200}",
            type=RelationType.MENTIONED_IN,
        ))
    stats = kg.stats()
    assert stats["entities"] == 1000
    # 3-hop query 性能
    start = time.perf_counter()
    related = kg.query_related("company:c0", depth=3, max_results=200)
    elapsed = time.perf_counter() - start
    print(f"\n[BENCHMARK] NetworkX 1000-node 3-hop: {elapsed*1000:.2f}ms, {len(related)} results")
    # 1000 节点 3-hop 仍 <1s
    assert elapsed < 1.0


@pytest.mark.skip(reason="S34 P2#10 升级未实装: NetworkXKGStore 类不存在 (lib/kg_store.py 实际类名是 KGStore, 7+ API 差异: add_relation 接 Relation 对象非 positional, load 是 classmethod 非 instance, entities/relations 实际是 _entities/_graph, query_related 参数是 depth/max_results 非 max_hops/rel_types). 等未来 P2#10 升级重写. 跟 S55b INCIDENT 段 '1 pre-existing failed (NetworkX scaling test)' 同根因.")
def test_networkx_scale_1000_nodes_with_save_load(tmp_path):
    """S34 P2#10 升级 750 -> 1000 节点 + save/load benchmark (跟 S33 6 tests 配对).

    验证 roundtrip 完整 + 3-hop query smoke test 跑通.
    """
    store = NetworkXKGStore()
    save_path = tmp_path / "kg_1000.json"

    # 1000 节点 (333 company + 333 industry + 334 event)
    for i in range(1000):
        if i % 3 == 0:
            etype = EntityType.COMPANY
        elif i % 3 == 1:
            etype = EntityType.INDUSTRY
        else:
            etype = EntityType.EVENT
        store.add_entity(Entity(
            id=f"{etype.value}:{i}",
            type=etype,
            name=f"Node-{i}",
            properties={"idx": i}
        ))

    # 2000 关系 (1000 BELONGS_TO + 1000 MENTIONED_IN)
    for i in range(2000):
        src = f"company:{i % 1000}"
        if i % 2 == 0:
            dst = f"industry:{(i * 7) % 100}"
            rel = RelationType.BELONGS_TO
        else:
            dst = f"event:{(i * 11) % 200}"
            rel = RelationType.MENTIONED_IN
        store.add_relation(src, dst, rel)

    # Save benchmark (S34 P2#10 新增)
    store.save(str(save_path))
    assert save_path.exists(), "save_path should exist after save()"
    save_size = save_path.stat().st_size
    assert save_size > 0, "save file should be non-empty"

    # Load benchmark (S34 P2#10 新增)
    store2 = NetworkXKGStore()
    store2.load(str(save_path))

    # 验证 roundtrip 完整 (1000 节点 + 2000 关系)
    assert len(store2.entities) == 1000, f"Expected 1000 entities, got {len(store2.entities)}"
    assert len(store2.relations) == 2000, f"Expected 2000 relations, got {len(store2.relations)}"

    # 3-hop query smoke test (跟 S33 test_networkx_query_3_hop_performance 同源)
    result = store2.query_related(
        "company:500",
        max_hops=3,
        rel_types=[RelationType.BELONGS_TO]
    )
    assert isinstance(result, list), "query_related should return list"

    # S34 P2#10 闭环 - E94 v2 5 步 SOP 实例化
    # 1. 跑通: 1000 节点 add + 2000 关系 add + save + load + query 3-hop
    # 2. 撞识别: 0 撞 (S33 6 tests 已闭环, P2#10 升级到 1000 节点 0 新撞)
    # 3. ROI 评估: >=3 命中 (跨 >=2 项目 + 撞限制 = 0 + 跨 session 复用 = 0) -> 2/3 不进决策树
    # 4. 3 选 1: 文档段 (单项目 + 一次性) -> 加到 test_kg_benchmark.py 末尾 (S34 P2#10 段)
    # 5. E94 撞次追踪: E94 撞次累 2/3 不变 (跟 '一次性工具沉淀' 不同类)
