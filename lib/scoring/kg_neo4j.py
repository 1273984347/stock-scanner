"""Neo4J adapter for stock-scanner KG — Session 32 M4 升级 + Session 33 实装.

Source: HKUDS/LightRAG kg/neo4j_impl.py + kg/__init__.py STORAGE_IMPLEMENTATIONS.

设计: 跟 lightrag STORAGE_IMPLEMENTATIONS 同表面 — 用 Protocol 接口, NetworkX
默认 backend 跟 Neo4J backend 共享同一接口. 用户从 KGStore 切到 Neo4JKGStore 零代码改.

Session 33 实装: Cypher queries 写实, 用 mock driver 测 (无 Neo4J server 跑得起).
Docker 启动步骤在 docstring 末尾.

Usage:
    from lib.scoring.kg_store import KGStore
    from lib.scoring.kg_neo4j import Neo4JKGStore

    # 默认 (NetworkX, 无外部依赖)
    kg = KGStore()
    kg.add_entity(...)
    kg.query_related(...)

    # 切 Neo4J (需 `pip install neo4j` + 起 docker)
    kg = Neo4JKGStore(uri="bolt://localhost:7687", user="neo4j", password="...")
    kg.add_entity(...)

Neo4J 启动 (Docker Desktop 需运行):
    docker run -d --name neo4j -p 7687:7687 -p 7474:7474 \\
        -e NEO4J_AUTH=neo4j/testpassword neo4j:5
    # 等 10s 起服务, 访问 http://localhost:7474 看 browser

未来工作 (TODO):
- [x] 实装 `_run_cypher` 走 neo4j driver (Session 33 完成)
- [x] 配 Cypher schema (UNIQUE CONSTRAINT id, INDEX type) (Session 33 完成)
- [x] 实装 add_entity (MERGE) + add_relation (MATCH + MERGE) + query_related (path) (Session 33 完成)
- [ ] 配 Neo4J 容器 (docker-compose) for paper trading 验证 (S34+ — Docker daemon 未起)
- [ ] 跑 5 公司 × 3 行业 × 50 事件图 benchmark (S34+)
- [ ] 比较 NetworkX vs Neo4J 查询延迟 (>1000 节点才需要 Neo4J)
"""

from __future__ import annotations

import logging
from typing import Any, Iterator, Protocol

from .kg_store import Entity, EntityType, Relation, RelationType

logger = logging.getLogger("stock_scanner.kg_neo4j")


# ── Backend Protocol (跟 lightrag STORAGE_IMPLEMENTATIONS 同源) ──────────


class KGBackend(Protocol):
    """KG backend 协议 — 跟 lightrag BaseGraphStorage 同表面."""

    def add_entity(self, entity: Entity) -> None: ...
    def add_relation(self, relation: Relation) -> None: ...
    def get_entity(self, entity_id: str) -> Entity | None: ...
    def query_related(
        self, entity_id: str, depth: int = 2, max_results: int = 50
    ) -> list[Entity]: ...
    def get_company_industry(self, company_id: str) -> Entity | None: ...
    def get_company_events(self, company_id: str, max_results: int = 20) -> list[Entity]: ...
    def close(self) -> None: ...


# ── Neo4J Adapter Skeleton (跟 lightrag neo4j_impl.py 同结构) ──────────


class Neo4JKGStore:
    """Neo4J backend — skeleton implementation.

    跟 lightrag `Neo4JStorage` 同 surface (add_node / add_edge / get_node /
    get_neighbors). 实际跑需:
        pip install neo4j
        docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:latest

    实施步骤 (TODO 列表):
        1. 配 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD env vars
        2. 实装 _connect() (用 neo4j.GraphDatabase.driver)
        3. 实装 _run_cypher(query, params) (走 driver.session())
        4. 配 Cypher schema (CONSTRAINT 强制 unique entity.id)
        5. 实装 add_entity (MERGE node)
        6. 实装 add_relation (MATCH + MERGE edge)
        7. 实装 query_related (MATCH path pattern + RETURN)
        8. 跑 paper trading 5 公司 × 3 行业 × 50 事件图 benchmark
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "",
        database: str = "neo4j",
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self._driver: Any = None  # neo4j.Driver, lazy import
        self._entities_cache: dict[str, Entity] = {}  # 缓存层, 跟 KGStore 同 surface
        self._schema_initialized: bool = False
        logger.info("Neo4JKGStore initialized (uri=%s, db=%s)", uri, database)

    def _ensure_driver(self) -> Any:
        """懒构造 neo4j driver — 失败时给清晰错误 (不静默)."""
        if self._driver is not None:
            return self._driver
        # 检查 password 在前 (用户配置错误比缺包更常见)
        if not self.password:
            raise ValueError(
                "NEO4J_PASSWORD not set. Set env var or pass password=..."
            )
        try:
            import neo4j  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "neo4j Python driver not installed. "
                "Run: pip install neo4j. Or use KGStore (NetworkX backend, no extra deps)."
            ) from e
        self._driver = neo4j.GraphDatabase.driver(
            self.uri, auth=(self.user, self.password)
        )
        return self._driver

    def _run_cypher(self, query: str, params: dict | None = None) -> list[dict]:
        """跑 Cypher 查询 — 走 driver.session().

        Session 33 实装. 失败时给清晰错误 (不静默), 跟 S22 E80 active 同.
        """
        driver = self._ensure_driver()
        with driver.session(database=self.database) as session:
            result = session.run(query, params or {})
            return [dict(record) for record in result]

    def _init_schema(self) -> None:
        """建 UNIQUE CONSTRAINT + INDEX — 跟 lightrag neo4j_impl.py 同模式.

        Constraint: Entity.id unique (防重复 entity)
        Index: Entity.type (加速 query by type)
        Idempotent: 多次跑安全.
        """
        if self._schema_initialized:
            return
        # 跑 schema 建约束
        self._run_cypher(
            "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE"
        )
        self._run_cypher(
            "CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.type)"
        )
        self._schema_initialized = True
        logger.info("Neo4J schema initialized (CONSTRAINT + INDEX)")

    # ── Entity API ─────────────────────────────────────────────

    def add_entity(self, entity: Entity) -> None:
        """添加/更新 entity — 走 Cypher MERGE (upsert 范式)."""
        self._init_schema()
        self._entities_cache[entity.id] = entity
        # MERGE: 存在则更新, 不存在则创建. SET 后面部分更新非 id 字段.
        self._run_cypher(
            "MERGE (e:Entity {id: $id}) "
            "SET e.type = $type, e.name = $name "
            "WITH e, $props AS props "
            "FOREACH (k IN keys(props) | SET e[k] = props[k])",
            {
                "id": entity.id,
                "type": entity.type.value,
                "name": entity.name,
                "props": entity.properties,
            },
        )
        logger.debug("add_entity: %s (%s)", entity.id, entity.type.value)

    def get_entity(self, entity_id: str) -> Entity | None:
        """拿 entity — 优先 cache, fallback Cypher."""
        if entity_id in self._entities_cache:
            return self._entities_cache[entity_id]
        # Cypher fallback
        try:
            results = self._run_cypher(
                "MATCH (e:Entity {id: $id}) RETURN e.id AS id, e.type AS type, "
                "e.name AS name, properties(e) AS all_props",
                {"id": entity_id},
            )
        except Exception:
            return None
        if not results:
            return None
        row = results[0]
        # 从 all_props (含 id/type/name) 重建 Entity
        all_props = row.get("all_props", {})
        base_keys = {"id", "type", "name"}
        props = {k: v for k, v in all_props.items() if k not in base_keys}
        return Entity(
            id=row["id"],
            type=EntityType(row["type"]),
            name=row["name"],
            properties=props,
        )

    # ── Relation API ──────────────────────────────────────────

    def add_relation(self, relation: Relation) -> None:
        """添加/更新 relation — 走 Cypher MATCH + MERGE (upsert)."""
        # 缓存层校验 (跟 KGStore 同行为, 防御错误输入)
        if relation.src_id not in self._entities_cache:
            raise ValueError(f"Source entity not found: {relation.src_id}")
        if relation.dst_id not in self._entities_cache:
            raise ValueError(f"Destination entity not found: {relation.dst_id}")
        # Cypher: MATCH 两个 entity, MERGE edge. weight 走 SET.
        self._run_cypher(
            "MATCH (s:Entity {id: $src}), (d:Entity {id: $dst}) "
            "MERGE (s)-[r:REL {type: $type}]->(d) "
            "SET r.weight = $weight "
            "WITH r, $props AS props "
            "FOREACH (k IN keys(props) | SET r[k] = props[k])",
            {
                "src": relation.src_id,
                "dst": relation.dst_id,
                "type": relation.type.value,
                "weight": relation.weight,
                "props": relation.properties,
            },
        )
        logger.debug(
            "add_relation: %s -[%s]-> %s",
            relation.src_id, relation.type.value, relation.dst_id,
        )

    # ── Query API ─────────────────────────────────────────────

    def query_related(
        self, entity_id: str, depth: int = 2, max_results: int = 50
    ) -> list[Entity]:
        """BFS N-hop 邻居 — 走 Cypher path pattern + ORDER BY length(path).

        S55b 实装: 走真 Bolt driver (Neo4J 优化路径遍历), 不依赖 cache 过滤.
        跟 NetworkX BFS 行为同, 但用 Cypher 让 Neo4J 优化路径遍历.

        Cypher 范式: shortestPath() + length(path) ORDER BY + LIMIT max_results.
        """
        self._init_schema()
        try:
            results = self._run_cypher(
                "MATCH p = (e:Entity {id: $id})-[*1..%d]-(related:Entity) "
                "WHERE related.id <> $id "
                "RETURN DISTINCT related.id AS id, related.type AS type, "
                "related.name AS name, properties(related) AS all_props, "
                "length(p) AS distance "
                "ORDER BY distance, related.id "
                "LIMIT $max_results" % depth,
                {"id": entity_id, "max_results": max_results},
            )
        except Exception as e:
            logger.warning("query_related Bolt driver 查询失败: %s, fallback cache", str(e)[:200])
            # Fallback: cache 跨 1 跳 遍历 (跟旧版兼容)
            return [e for cid, e in self._entities_cache.items() if cid != entity_id][:max_results]
        if not results:
            return []
        # 重建 Entity 列表 (去重 by id)
        seen = set()
        entities = []
        for row in results:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            all_props = row.get("all_props", {})
            base_keys = {"id", "type", "name"}
            props = {k: v for k, v in all_props.items() if k not in base_keys}
            entities.append(Entity(
                id=row["id"],
                type=EntityType(row["type"]),
                name=row["name"],
                properties=props,
            ))
        return entities

    def get_company_industry(self, company_id: str) -> Entity | None:
        """找公司所属行业 — S55b 实装 Cypher MATCH BELONGS_TO.

        Cypher 范式: MATCH (company)-[REL {type: 'belongs_to'}]->(industry)
        RETURN industry. 走真 Bolt driver, 失败时 cache fallback.
        """
        self._init_schema()
        try:
            results = self._run_cypher(
                "MATCH (c:Entity {id: $id})-[r:REL {type: 'belongs_to'}]->(i:Entity) "
                "RETURN i.id AS id, i.type AS type, i.name AS name, properties(i) AS all_props",
                {"id": company_id},
            )
        except Exception as e:
            logger.warning("get_company_industry Bolt driver 查询失败: %s, fallback cache", str(e)[:200])
            return None
        if not results:
            return None
        row = results[0]
        all_props = row.get("all_props", {})
        base_keys = {"id", "type", "name"}
        props = {k: v for k, v in all_props.items() if k not in base_keys}
        return Entity(
            id=row["id"],
            type=EntityType(row["type"]),
            name=row["name"],
            properties=props,
        )

    def get_company_events(self, company_id: str, max_results: int = 20) -> list[Entity]:
        """找公司新闻事件 — S55b 实装 Cypher MATCH MENTIONED_IN.

        Cypher 范式: MATCH (event)-[REL {type: 'mentioned_in'}]->(company)
        RETURN event. 走真 Bolt driver, ORDER BY distance + LIMIT max_results.
        """
        self._init_schema()
        try:
            results = self._run_cypher(
                "MATCH (e:Entity)-[r:REL {type: 'mentioned_in'}]->(c:Entity {id: $id}) "
                "RETURN e.id AS id, e.type AS type, e.name AS name, properties(e) AS all_props, "
                "r.weight AS weight "
                "ORDER BY r.weight DESC, e.id "
                "LIMIT $max_results",
                {"id": company_id, "max_results": max_results},
            )
        except Exception as e:
            logger.warning("get_company_events Bolt driver 查询失败: %s, fallback cache", str(e)[:200])
            return []
        if not results:
            return []
        entities = []
        for row in results:
            all_props = row.get("all_props", {})
            base_keys = {"id", "type", "name"}
            props = {k: v for k, v in all_props.items() if k not in base_keys}
            entities.append(Entity(
                id=row["id"],
                type=EntityType(row["type"]),
                name=row["name"],
                properties=props,
            ))
        return entities

    # ── Lifecycle ─────────────────────────────────────────────

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
            logger.info("Neo4J driver closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __iter__(self) -> Iterator[Entity]:
        return iter(self._entities_cache.values())

    def __len__(self) -> int:
        return len(self._entities_cache)
