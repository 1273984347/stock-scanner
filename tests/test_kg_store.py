"""Tests for KGStore — Session 31 M4 stock-scanner 知识图谱. 跟 lightrag 同源测试模式."""

import json
import tempfile
from pathlib import Path

import pytest

from lib.kg_store import (
    Entity,
    EntityType,
    KGStore,
    Relation,
    RelationType,
)


@pytest.fixture
def fresh_kg() -> KGStore:
    return KGStore()


@pytest.fixture
def sample_kg() -> KGStore:
    """5 公司 × 3 行业 × 50 事件 (M4 retro 估的 KG 规模).

    Returns a populated KG ready for query tests.
    """
    kg = KGStore()
    # 3 行业
    industries = [
        Entity(id="industry:liquor", type=EntityType.INDUSTRY, name="白酒"),
        Entity(id="industry:tech", type=EntityType.INDUSTRY, name="科技"),
        Entity(id="industry:finance", type=EntityType.INDUSTRY, name="金融"),
    ]
    # 5 公司
    companies = [
        Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台", properties={"symbol": "600519"}),
        Entity(id="company:000858", type=EntityType.COMPANY, name="五粮液", properties={"symbol": "000858"}),
        Entity(id="company:00700", type=EntityType.COMPANY, name="腾讯", properties={"symbol": "00700"}),
        Entity(id="company:BABA", type=EntityType.COMPANY, name="阿里巴巴", properties={"symbol": "BABA"}),
        Entity(id="company:600036", type=EntityType.COMPANY, name="招商银行", properties={"symbol": "600036"}),
    ]
    for e in industries + companies:
        kg.add_entity(e)

    # 公司 → 行业 (5 relations)
    belongs = [
        ("company:600519", "industry:liquor"),
        ("company:000858", "industry:liquor"),
        ("company:00700", "industry:tech"),
        ("company:BABA", "industry:tech"),
        ("company:600036", "industry:finance"),
    ]
    for src, dst in belongs:
        kg.add_relation(Relation(src_id=src, dst_id=dst, type=RelationType.BELONGS_TO))

    # 10 事件 (简化 5×3×50 → 10 events, 测试足够)
    events = [
        Entity(id="event:news-001", type=EntityType.EVENT, name="茅台Q1业绩超预期", properties={"date": "2026-04-15"}),
        Entity(id="event:news-002", type=EntityType.EVENT, name="五粮液股东大会", properties={"date": "2026-05-20"}),
        Entity(id="event:news-003", type=EntityType.EVENT, name="腾讯回购10亿", properties={"date": "2026-03-10"}),
        Entity(id="event:news-004", type=EntityType.EVENT, name="阿里云涨价", properties={"date": "2026-04-22"}),
        Entity(id="event:news-005", type=EntityType.EVENT, name="招行分红方案", properties={"date": "2026-05-30"}),
    ]
    for e in events:
        kg.add_entity(e)
    # 事件 → 公司 (5 relations)
    mentioned = [
        ("event:news-001", "company:600519"),
        ("event:news-002", "company:000858"),
        ("event:news-003", "company:00700"),
        ("event:news-004", "company:BABA"),
        ("event:news-005", "company:600036"),
    ]
    for src, dst in mentioned:
        kg.add_relation(Relation(src_id=src, dst_id=dst, type=RelationType.MENTIONED_IN))
    return kg


# ── Entity tests ──────────────────────────────────────────


def test_add_and_get_entity(fresh_kg):
    e = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    fresh_kg.add_entity(e)
    got = fresh_kg.get_entity("company:600519")
    assert got is not None
    assert got.name == "贵州茅台"
    assert got.type == EntityType.COMPANY


def test_upsert_entity_overwrites(fresh_kg):
    """同 ID 二次 add 应该覆盖 (跟 lightrag upsert 范式同)."""
    e1 = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    e2 = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台 (Kweichow Moutai)")
    fresh_kg.add_entity(e1)
    fresh_kg.add_entity(e2)
    assert fresh_kg.get_entity("company:600519").name == "贵州茅台 (Kweichow Moutai)"


def test_entity_to_from_dict():
    e = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台", properties={"symbol": "600519", "market_cap": 2_000_000_000_000})
    d = e.to_dict()
    assert d["id"] == "company:600519"
    assert d["type"] == "company"
    assert d["symbol"] == "600519"
    e2 = Entity.from_dict(d)
    assert e2 == e
    assert e2.properties == {"symbol": "600519", "market_cap": 2_000_000_000_000}


# ── Relation tests ────────────────────────────────────────


def test_add_relation(sample_kg):
    """M4 sample KG 应该有 10 entities + 10 relations."""
    assert sample_kg.get_company_industry("company:600519").name == "白酒"


def test_relation_requires_existing_entities(fresh_kg):
    """未注册的 entity 关系应该报错 — 跟 lightrag 同源错误处理."""
    e = Entity(id="company:600519", type=EntityType.COMPANY, name="贵州茅台")
    fresh_kg.add_entity(e)
    with pytest.raises(ValueError, match="Destination entity not found"):
        fresh_kg.add_relation(Relation(src_id="company:600519", dst_id="company:000858", type=RelationType.BELONGS_TO))


# ── Query tests ───────────────────────────────────────────


def test_get_company_industry(sample_kg):
    assert sample_kg.get_company_industry("company:600519").name == "白酒"
    assert sample_kg.get_company_industry("company:BABA").name == "科技"


def test_get_company_events(sample_kg):
    events = sample_kg.get_company_events("company:600519")
    assert len(events) == 1
    assert events[0].name == "茅台Q1业绩超预期"


def test_query_related_1_hop(sample_kg):
    """1-hop: 茅台 → 行业(白酒) + 事件(茅台Q1业绩超预期)."""
    related = sample_kg.query_related("company:600519", depth=1)
    names = {r.name for r in related}
    assert "白酒" in names
    assert "茅台Q1业绩超预期" in names


def test_query_related_2_hop(sample_kg):
    """2-hop: 茅台 → 行业(白酒) → 五粮液 (同行)."""
    related = sample_kg.query_related("company:600519", depth=2)
    names = {r.name for r in related}
    assert "五粮液" in names  # 同行


def test_query_related_unknown_entity(sample_kg):
    """未注册的 entity 应该返回空列表, 不报错."""
    related = sample_kg.query_related("company:UNKNOWN", depth=2)
    assert related == []


# ── Persistence tests ─────────────────────────────────────


def test_save_and_load(sample_kg):
    """JSON 持久化 round-trip — 跟 lightrag json_impl.py 同源."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kg.json"
        sample_kg.save(path)
        assert path.exists()
        loaded = KGStore.load(path)
        assert loaded.stats() == sample_kg.stats()
        # 验证关键 entity/relation 还在
        assert loaded.get_company_industry("company:600519").name == "白酒"
        events = loaded.get_company_events("company:00700")
        assert len(events) == 1
        assert events[0].name == "腾讯回购10亿"


def test_save_creates_parent_dir(sample_kg):
    """save 应该自动创建父目录 — 跟 lightrag 同源."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "deeper" / "kg.json"
        sample_kg.save(path)
        assert path.exists()


# ── Stats tests ────────────────────────────────────────────


def test_stats(sample_kg):
    s = sample_kg.stats()
    assert s["entities"] == 13  # 5 companies + 3 industries + 5 events
    assert s["companies"] == 5
    assert s["industries"] == 3
    assert s["events"] == 5
    assert s["edges"] == 10  # 5 BELONGS_TO + 5 MENTIONED_IN


def test_len_iter(sample_kg):
    assert len(sample_kg) == 13
    entities = list(sample_kg)
    assert all(isinstance(e, Entity) for e in entities)
