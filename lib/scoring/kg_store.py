"""Lightweight in-memory Knowledge Graph for stock-scanner — Session 31 M4.

Source: HKUDS/LightRAG 5 大范式 #4 (4-type storage + 12 backend) + example-insert-custom-kg.py.

设计取舍 (跟 LightRAG STORAGE_IMPLEMENTATIONS 同结构, 选 NetworkX 默认 backend):
- 不强依赖 Neo4J / Milvus server (避免 stock-scanner paper trading 期引入重基础设施)
- 用 networkx.DiGraph 内存图 + JSON 持久化 (跟 lightrag networkx_impl.py + json_impl.py 同源)
- 3 entity type: COMPANY / INDUSTRY / EVENT
- 4 relation type: BELONGS_TO / MENTIONED_IN / SUPPLIES_TO / SUBSIDIARY_OF (extensible)
- API 跟 lightrag ainsert_custom_kg 同表面 — add_entity / add_relation / query_related

未来 backend 切换: 若 paper trading 验证后 KG 价值大, 换 Neo4J 后端走 lightrag kg/neo4j_impl.py 范式。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

import networkx as nx

logger = logging.getLogger("stock_scanner.kg")


class EntityType(str, Enum):
    """3 类实体 — 跟 lightrag entity 抽象同源."""

    COMPANY = "company"        # 股票 / 公司
    INDUSTRY = "industry"      # 行业 / 板块
    EVENT = "event"            # 新闻 / 公告 / 财报


class RelationType(str, Enum):
    """4 类关系 — 跟 lightrag relation 抽象同源."""

    BELONGS_TO = "belongs_to"          # COMPANY → INDUSTRY (公司属于行业)
    MENTIONED_IN = "mentioned_in"      # COMPANY → EVENT (公司被新闻提及)
    SUPPLIES_TO = "supplies_to"        # COMPANY → COMPANY (供应链)
    SUBSIDIARY_OF = "subsidiary_of"    # COMPANY → COMPANY (子公司)


@dataclass
class Entity:
    """实体 — 跟 lightrag entity dict 范式同."""

    id: str                         # 唯一 ID (e.g. "company:600519", "industry:白酒", "event:news-2026-06-01")
    type: EntityType
    name: str
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type.value, "name": self.name, **self.properties}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        # properties 字段 = 除了 id/type/name 之外的所有字段
        base_keys = {"id", "type", "name"}
        props = {k: v for k, v in data.items() if k not in base_keys}
        return cls(id=data["id"], type=EntityType(data["type"]), name=data["name"], properties=props)


@dataclass
class Relation:
    """关系 — 跟 lightrag relation 范式同."""

    src_id: str
    dst_id: str
    type: RelationType
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src_id,
            "dst": self.dst_id,
            "type": self.type.value,
            "weight": self.weight,
            **self.properties,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Relation":
        base_keys = {"src", "dst", "type", "weight"}
        props = {k: v for k, v in data.items() if k not in base_keys}
        return cls(
            src_id=data["src"],
            dst_id=data["dst"],
            type=RelationType(data["type"]),
            weight=data.get("weight", 1.0),
            properties=props,
        )


class KGStore:
    """In-memory KG 跟 lightrag STORAGE_IMPLEMENTATIONS[GRAPH_STORAGE] 同表面.

    Backend: networkx.DiGraph (默认; 可换 Neo4J 通过本类继承实现)
    Persistence: JSON file (跟 lightrag json_impl.py 同; 可换 Postgres via kg/postgres_impl.py)

    用法 (跟 lightrag ainsert_custom_kg 范式同):
        kg = KGStore()
        kg.add_entity(Entity(id="company:600519", type=COMPANY, name="贵州茅台", properties={"symbol": "600519"}))
        kg.add_entity(Entity(id="industry:liquor", type=INDUSTRY, name="白酒"))
        kg.add_relation(Relation(src_id="company:600519", dst_id="industry:liquor", type=BELONGS_TO))
        related = kg.query_related("company:600519", depth=2)
    """

    def __init__(self):
        self._graph = nx.DiGraph()
        self._entities: dict[str, Entity] = {}

    # ── Entity API (跟 lightrag upsert_entity 同源) ─────────────

    def add_entity(self, entity: Entity) -> None:
        """Upsert entity — 同 ID 覆盖."""
        self._entities[entity.id] = entity
        self._graph.add_node(
            entity.id,
            type=entity.type.value,
            name=entity.name,
            **entity.properties,
        )

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    # ── Relation API (跟 lightrag upsert_relation 同源) ─────────

    def add_relation(self, relation: Relation) -> None:
        """Upsert relation — 同 (src, dst, type) 覆盖."""
        if relation.src_id not in self._entities:
            raise ValueError(f"Source entity not found: {relation.src_id}")
        if relation.dst_id not in self._entities:
            raise ValueError(f"Destination entity not found: {relation.dst_id}")
        self._graph.add_edge(
            relation.src_id,
            relation.dst_id,
            type=relation.type.value,
            weight=relation.weight,
            **relation.properties,
        )

    # ── Query API (跟 lightrag aquery 同表面) ───────────────

    def query_related(self, entity_id: str, depth: int = 2, max_results: int = 50) -> list[Entity]:
        """BFS 找 N-hop 邻居 — 跟 lightrag kg query 同源.

        跨所有关系类型, 按 weight 排序. 不走 relation type filter (跟 lightrag default mix mode 同).
        """
        if entity_id not in self._graph:
            return []
        # BFS 遍历
        visited = {entity_id}
        current_layer = {entity_id}
        results: list[tuple[Entity, float]] = []
        for _ in range(depth):
            next_layer = set()
            for node in current_layer:
                for neighbor in self._graph.successors(node):
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    next_layer.add(neighbor)
                    edge_data = self._graph.edges[node, neighbor]
                    weight = edge_data.get("weight", 1.0)
                    results.append((self._entities[neighbor], weight))
                for neighbor in self._graph.predecessors(node):
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    next_layer.add(neighbor)
                    edge_data = self._graph.edges[neighbor, node]
                    weight = edge_data.get("weight", 1.0)
                    results.append((self._entities[neighbor], weight))
            current_layer = next_layer
        # 按 weight desc 排序
        results.sort(key=lambda x: x[1], reverse=True)
        return [entity for entity, _ in results[:max_results]]

    def get_company_industry(self, company_id: str) -> Entity | None:
        """找公司所属行业 — 1-hop BELONGS_TO 查询."""
        if company_id not in self._graph:
            return None
        for successor in self._graph.successors(company_id):
            edge = self._graph.edges[company_id, successor]
            if edge.get("type") == RelationType.BELONGS_TO.value:
                return self._entities[successor]
        return None

    def get_company_events(self, company_id: str, max_results: int = 20) -> list[Entity]:
        """找公司被提及的新闻事件 — 1-hop MENTIONED_IN 反向查询."""
        if company_id not in self._graph:
            return []
        events = []
        for predecessor in self._graph.predecessors(company_id):
            edge = self._graph.edges[predecessor, company_id]
            if edge.get("type") == RelationType.MENTIONED_IN.value:
                events.append(self._entities[predecessor])
        return events[:max_results]

    # ── Persistence (跟 lightrag json_impl.py 同源) ───────────

    def to_dict(self) -> dict[str, Any]:
        """导出为 dict (JSON 序列化)."""
        return {
            "entities": [e.to_dict() for e in self._entities.values()],
            "relations": [
                {
                    "src": u,
                    "dst": v,
                    **{k: val for k, val in data.items() if k in ("type", "weight")},
                    **{
                        k: val
                        for k, val in data.items()
                        if k not in ("type", "weight")
                    },
                }
                for u, v, data in self._graph.edges(data=True)
            ],
        }

    def save(self, path: str | Path) -> None:
        """存为 JSON file — 跟 lightrag json_impl.py save 同源."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("KGStore saved to %s (%d entities, %d edges)", p, len(self._entities), self._graph.number_of_edges())

    @classmethod
    def load(cls, path: str | Path) -> "KGStore":
        """从 JSON file 加载 — 跟 lightrag json_impl.py load 同源."""
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        kg = cls()
        for entity_data in data.get("entities", []):
            kg.add_entity(Entity.from_dict(entity_data))
        for relation_data in data.get("relations", []):
            base_keys = {"src", "dst", "type", "weight"}
            props = {k: v for k, v in relation_data.items() if k not in base_keys}
            kg.add_relation(Relation(
                src_id=relation_data["src"],
                dst_id=relation_data["dst"],
                type=RelationType(relation_data["type"]),
                weight=relation_data.get("weight", 1.0),
                properties=props,
            ))
        logger.info("KGStore loaded from %s (%d entities, %d edges)", p, len(kg._entities), kg._graph.number_of_edges())
        return kg

    # ── Stats (跟 lightrag kg.get_stats 同源) ───────────────

    def stats(self) -> dict[str, int]:
        return {
            "entities": len(self._entities),
            "edges": self._graph.number_of_edges(),
            "companies": sum(1 for e in self._entities.values() if e.type == EntityType.COMPANY),
            "industries": sum(1 for e in self._entities.values() if e.type == EntityType.INDUSTRY),
            "events": sum(1 for e in self._entities.values() if e.type == EntityType.EVENT),
        }

    def __iter__(self) -> Iterator[Entity]:
        return iter(self._entities.values())

    def __len__(self) -> int:
        return len(self._entities)
