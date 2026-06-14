"""Tests for Neo4JKGStore — Session 32 M4 升级 + Session 33 实装.

Session 33: add_entity / add_relation / get_entity / _init_schema 实装 Cypher
(MERGE / MATCH / CONSTRAINT). 用 mock driver 验证 Cypher 构造正确,
不实跑 Neo4J (Docker daemon 没起). 真 Neo4J 集成测试需 docker + 显式 skip.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from lib.kg_neo4j import Neo4JKGStore
from lib.kg_store import Entity, EntityType, Relation, RelationType


@pytest.fixture
def neo4j_store():
    return Neo4JKGStore(uri="bolt://localhost:7687", user="neo4j", password="test")


@pytest.fixture
def mock_driver(monkeypatch):
    """Mock neo4j driver 全栈 + session context manager."""
    mock_neo4j = MagicMock()
    mock_driver_instance = MagicMock()
    mock_session = MagicMock()
    mock_neo4j.GraphDatabase.driver.return_value = mock_driver_instance
    # session() 是 context manager
    mock_driver_instance.session.return_value.__enter__.return_value = mock_session
    mock_driver_instance.session.return_value.__exit__.return_value = False
    monkeypatch.setitem(sys.modules, "neo4j", mock_neo4j)
    return mock_driver_instance, mock_session


# ── Cache 行为 (跟 KGStore 同 surface) ─────────────────────


def test_add_entity_caches(neo4j_store, mock_driver):
    """add_entity 应 cache + 跑 Cypher MERGE."""
    mock_drv, mock_session = mock_driver
    e = Entity(
        id="company:600519",
        type=EntityType.COMPANY,
        name="贵州茅台",
        properties={"symbol": "600519"},
    )
    neo4j_store.add_entity(e)
    # Cache hit
    assert neo4j_store.get_entity("company:600519").name == "贵州茅台"
    # Cypher 跑了 (schema init + add_entity)
    assert mock_session.run.call_count >= 2  # 1 schema init + 1 add_entity


def test_add_entity_cypher_has_merge(neo4j_store, mock_driver):
    """add_entity 的 Cypher 应含 MERGE (upsert 范式) + SET."""
    mock_drv, mock_session = mock_driver
    e = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    neo4j_store.add_entity(e)
    # 找含 MERGE 的 query
    merge_calls = [
        call for call in mock_session.run.call_args_list
        if "MERGE" in call.args[0]
    ]
    assert len(merge_calls) >= 1, f"Expected MERGE query, got: {mock_session.run.call_args_list}"
    query = merge_calls[0].args[0]
    assert "Entity" in query
    assert "id: $id" in query


def test_add_relation_validates(neo4j_store, mock_driver):
    """未注册的 entity 关系应该报错 (跟 KGStore 行为同)."""
    e = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    neo4j_store.add_entity(e)
    with pytest.raises(ValueError, match="Destination entity not found"):
        neo4j_store.add_relation(Relation(src_id="company:600519", dst_id="company:000858", type=RelationType.BELONGS_TO))


def test_add_relation_cypher_has_match_merge(neo4j_store, mock_driver):
    """add_relation 的 Cypher 应含 MATCH + MERGE (upsert edge)."""
    mock_drv, mock_session = mock_driver
    e1 = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    e2 = Entity(id="industry:liquor", type=EntityType.INDUSTRY, name="白酒")
    neo4j_store.add_entity(e1)
    neo4j_store.add_entity(e2)
    mock_session.reset_mock()
    neo4j_store.add_relation(Relation(
        src_id="company:600519",
        dst_id="industry:liquor",
        type=RelationType.BELONGS_TO,
        weight=1.0,
    ))
    # 找含 MATCH + MERGE 的 query
    match_merge_calls = [
        call for call in mock_session.run.call_args_list
        if "MATCH" in call.args[0] and "MERGE" in call.args[0]
    ]
    assert len(match_merge_calls) == 1
    query = match_merge_calls[0].args[0]
    assert "REL" in query
    assert "weight = $weight" in query


def test_init_schema_runs_once(neo4j_store, mock_driver):
    """_init_schema 应只在第一次 add_entity 时跑 (idempotent)."""
    mock_drv, mock_session = mock_driver
    e1 = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    e2 = Entity(id="company:000858", type=EntityType.COMPANY, name="五粮液")
    neo4j_store.add_entity(e1)
    # 第二次 add_entity 不再跑 schema
    schema_calls_before = sum(
        1 for call in mock_session.run.call_args_list
        if "CONSTRAINT" in call.args[0] or "INDEX" in call.args[0]
    )
    neo4j_store.add_entity(e2)
    schema_calls_after = sum(
        1 for call in mock_session.run.call_args_list
        if "CONSTRAINT" in call.args[0] or "INDEX" in call.args[0]
    )
    assert schema_calls_before == schema_calls_after, "Schema should only init once"


# ── Query (仍未实装 Cypher) ─────────────────────────────────


def test_query_related_runs_cypher(neo4j_store, mock_driver):
    """S55b 实装: query_related 走 Cypher shortestPath (BFS N-hop 邻居).

    Mock session.run 返 1 个 record (company:000858 五粮液), 验证 query_related
    走真 Bolt driver 拿 Cypher result, 不依赖 cache 遍历.
    """
    mock_drv, mock_session = mock_driver
    e1 = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    e2 = Entity(id="company:000858", type=EntityType.COMPANY, name="五粮液")
    neo4j_store.add_entity(e1)
    neo4j_store.add_entity(e2)
    # Mock Cypher return: 1 个 record (五粮液)
    mock_record = MagicMock()
    mock_record.__getitem__ = lambda self, key: {
        "id": "company:000858", "type": "company", "name": "五粮液",
        "all_props": {"id": "company:000858", "type": "company", "name": "五粮液"},
        "distance": 1,
    }[key]
    mock_record.keys = lambda: ["id", "type", "name", "all_props", "distance"]
    mock_record.values = lambda: ["company:000858", "company", "五粮液", {"id": "company:000858", "type": "company", "name": "五粮液"}, 1]
    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter([mock_record])
    mock_session.run.return_value = mock_result
    results = neo4j_store.query_related("company:600519", depth=2, max_results=10)
    names = {r.name for r in results}
    assert "五粮液" in names
    # 验证 Cypher query 含 shortestPath 范式
    last_call = mock_session.run.call_args_list[-1]
    assert "MATCH p" in last_call.args[0]
    assert "shortestPath" not in last_call.args[0]  # 我们用 [*1..depth] 不是 shortestPath()
    assert "[*1..2]" in last_call.args[0]


def test_get_company_industry_runs_cypher(neo4j_store, mock_driver):
    """S55b 实装: get_company_industry 走 Cypher MATCH BELONGS_TO.

    Mock session.run 返 1 个 record (industry:liquor 白酒), 验证 query 走真 Bolt driver.
    """
    mock_drv, mock_session = mock_driver
    e1 = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    e2 = Entity(id="industry:liquor", type=EntityType.INDUSTRY, name="白酒")
    neo4j_store.add_entity(e1)
    neo4j_store.add_entity(e2)
    neo4j_store.add_relation(Relation(src_id="company:600519", dst_id="industry:liquor", type=RelationType.BELONGS_TO))
    # Mock Cypher return: 1 个 record (白酒)
    mock_record = MagicMock()
    mock_record.__getitem__ = lambda self, key: {
        "id": "industry:liquor", "type": "industry", "name": "白酒",
        "all_props": {"id": "industry:liquor", "type": "industry", "name": "白酒"},
    }[key]
    mock_record.keys = lambda: ["id", "type", "name", "all_props"]
    mock_record.values = lambda: ["industry:liquor", "industry", "白酒", {"id": "industry:liquor", "type": "industry", "name": "白酒"}]
    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter([mock_record])
    mock_session.run.return_value = mock_result
    industry = neo4j_store.get_company_industry("company:600519")
    assert industry is not None
    assert industry.name == "白酒"
    # 验证 Cypher query 含 MATCH BELONGS_TO
    last_call = mock_session.run.call_args_list[-1]
    assert "MATCH (c:Entity" in last_call.args[0]
    assert "BELONGS_TO" in last_call.args[0].upper() or "belongs_to" in last_call.args[0]


def test_get_company_events_runs_cypher(neo4j_store, mock_driver):
    """S55b 实装: get_company_events 走 Cypher MATCH MENTIONED_IN.

    Mock session.run 返 0 records (无事件), 验证 query 走真 Bolt driver 返 [].
    """
    mock_drv, mock_session = mock_driver
    # Mock Cypher return: 0 records (空列表)
    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter([])
    mock_session.run.return_value = mock_result
    events = neo4j_store.get_company_events("company:600519")
    assert events == []
    # 验证 Cypher query 含 MATCH MENTIONED_IN
    last_call = mock_session.run.call_args_list[-1]
    assert "MATCH (e:Entity)" in last_call.args[0]
    assert "mentioned_in" in last_call.args[0]


# ── Driver 集成 ────────────────────────────────────────────


def test_ensure_driver_raises_without_password():
    """没 password 时应给清晰错误, 不静默."""
    store = Neo4JKGStore(uri="bolt://localhost:7687", user="neo4j", password="")
    with pytest.raises(ValueError, match="NEO4J_PASSWORD not set"):
        store._ensure_driver()


def test_ensure_driver_raises_without_neo4j_package():
    """没装 neo4j 时应给 ImportError, 不静默."""
    store = Neo4JKGStore(uri="bolt://localhost:7687", user="neo4j", password="test")
    with patch.dict(sys.modules, {"neo4j": None}):
        with pytest.raises(ImportError, match="neo4j Python driver not installed"):
            store._ensure_driver()


def test_ensure_driver_lazy_success(monkeypatch):
    """装好 neo4j + 有 password 时, 第一次 _ensure_driver 才连."""
    mock_neo4j = MagicMock()
    mock_driver = MagicMock()
    mock_neo4j.GraphDatabase.driver.return_value = mock_driver
    monkeypatch.setitem(sys.modules, "neo4j", mock_neo4j)

    store = Neo4JKGStore(uri="bolt://test", user="neo4j", password="test")
    driver = store._ensure_driver()
    assert driver is mock_driver
    assert store._ensure_driver() is mock_driver  # cached
    mock_neo4j.GraphDatabase.driver.assert_called_once_with(
        "bolt://test", auth=("neo4j", "test")
    )


# ── Lifecycle ───────────────────────────────────────────────


def test_close_idempotent(neo4j_store):
    neo4j_store.close()
    neo4j_store.close()


def test_context_manager(neo4j_store):
    with neo4j_store as store:
        assert store is neo4j_store


def test_len_and_iter(neo4j_store, mock_driver):
    e1 = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    e2 = Entity(id="industry:liquor", type=EntityType.INDUSTRY, name="白酒")
    neo4j_store.add_entity(e1)
    neo4j_store.add_entity(e2)
    assert len(neo4j_store) == 2
    assert len(list(neo4j_store)) == 2
