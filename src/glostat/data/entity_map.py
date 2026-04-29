from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

# INV-GS-002: find_companies result is permanent cache. Re-call forbidden.
# Storage: parquet (write-once-read-many).

log: Final = structlog.get_logger(__name__)

ListingType = Literal["common", "preferred", "ADR", "GDR", "DR", "dual_primary"]


@dataclass(frozen=True, slots=True)
class CrossListing:
    rp_entity_id: str
    primary_uaid: str
    listings: tuple[str, ...]
    relationships: tuple[tuple[str, ListingType], ...]

    def listing_type_for(self, uaid: str) -> ListingType | None:
        for u, rel in self.relationships:
            if u == uaid:
                return rel
        return None


# Sprint 1 PR #4: extended schema with sector + market_cap_usd. Older parquet
# files written by Sprint 0/1 PR #1-#3 still load via _row_to_record() — both
# new fields default to "UNKNOWN" / 0.0 and must be refreshed by the universe
# build job. Schema migration is forward-only (no parquet rewrite).
@dataclass(frozen=True, slots=True)
class EntityRecord:
    rp_entity_id: str
    canonical_name: str
    primary_uaid: str
    cross_listing: CrossListing
    bigdata_search_query: str
    resolved_at: datetime
    sector: str = "UNKNOWN"
    market_cap_usd: float = 0.0


def _record_to_row(r: EntityRecord) -> dict[str, object]:
    return {
        "rp_entity_id": r.rp_entity_id,
        "canonical_name": r.canonical_name,
        "primary_uaid": r.primary_uaid,
        "listings": list(r.cross_listing.listings),
        "relationships_uaid": [u for u, _ in r.cross_listing.relationships],
        "relationships_type": [rel for _, rel in r.cross_listing.relationships],
        "bigdata_search_query": r.bigdata_search_query,
        "resolved_at": r.resolved_at,
        "sector": r.sector,
        "market_cap_usd": float(r.market_cap_usd),
    }


def _row_to_record(row: dict[str, object]) -> EntityRecord:
    rels_uaid = list(row["relationships_uaid"])  # type: ignore[arg-type]
    rels_type = list(row["relationships_type"])  # type: ignore[arg-type]
    relationships = tuple(zip(rels_uaid, rels_type, strict=True))
    cross = CrossListing(
        rp_entity_id=str(row["rp_entity_id"]),
        primary_uaid=str(row["primary_uaid"]),
        listings=tuple(row["listings"]),  # type: ignore[arg-type]
        relationships=relationships,  # type: ignore[arg-type]
    )
    resolved = row["resolved_at"]
    if not isinstance(resolved, datetime):
        resolved = datetime.fromisoformat(str(resolved))
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=UTC)
    sector = str(row.get("sector") or "UNKNOWN")
    market_cap_raw = row.get("market_cap_usd")
    market_cap_usd = float(market_cap_raw) if market_cap_raw is not None else 0.0
    return EntityRecord(
        rp_entity_id=str(row["rp_entity_id"]),
        canonical_name=str(row["canonical_name"]),
        primary_uaid=str(row["primary_uaid"]),
        cross_listing=cross,
        bigdata_search_query=str(row["bigdata_search_query"]),
        resolved_at=resolved,
        sector=sector,
        market_cap_usd=market_cap_usd,
    )


_SCHEMA: Final = pa.schema(
    [
        ("rp_entity_id", pa.string()),
        ("canonical_name", pa.string()),
        ("primary_uaid", pa.string()),
        ("listings", pa.list_(pa.string())),
        ("relationships_uaid", pa.list_(pa.string())),
        ("relationships_type", pa.list_(pa.string())),
        ("bigdata_search_query", pa.string()),
        ("resolved_at", pa.timestamp("us", tz="UTC")),
        ("sector", pa.string()),
        ("market_cap_usd", pa.float64()),
    ]
)


@dataclass(slots=True)
class EntityMap:
    cache_path: Path
    _by_query: dict[str, EntityRecord] = field(default_factory=dict)
    _by_entity: dict[str, EntityRecord] = field(default_factory=dict)
    _by_uaid: dict[str, EntityRecord] = field(default_factory=dict)
    _dirty: bool = False

    @classmethod
    def load(cls, cache_path: Path | str) -> EntityMap:
        path = Path(cache_path)
        instance = cls(cache_path=path)
        if not path.exists():
            log.info("entity_map.no_cache", path=str(path))
            return instance
        table = pq.read_table(path)
        for row in table.to_pylist():
            record = _row_to_record(row)
            instance._index(record)
        log.info("entity_map.loaded", path=str(path), rows=len(instance._by_entity))
        return instance

    def get_by_query(self, query: str) -> EntityRecord | None:
        return self._by_query.get(_normalize(query))

    def get_by_entity_id(self, rp_entity_id: str) -> EntityRecord | None:
        return self._by_entity.get(rp_entity_id)

    def get_by_uaid(self, uaid: str) -> EntityRecord | None:
        return self._by_uaid.get(uaid)

    def upsert(self, record: EntityRecord) -> None:
        existing = self._by_entity.get(record.rp_entity_id)
        if existing == record:
            return
        if existing is not None:
            log.warning(
                "entity_map.overwrite",
                entity=record.rp_entity_id,
                old_query=existing.bigdata_search_query,
                new_query=record.bigdata_search_query,
            )
        self._index(record)
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [_record_to_row(r) for r in self._by_entity.values()]
        table = pa.Table.from_pylist(rows, schema=_SCHEMA)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(self.cache_path)
        self._dirty = False
        log.info("entity_map.flushed", path=str(self.cache_path), rows=len(rows))

    def __len__(self) -> int:
        return len(self._by_entity)

    def __iter__(self):
        return iter(self._by_entity.values())

    def _index(self, record: EntityRecord) -> None:
        self._by_query[_normalize(record.bigdata_search_query)] = record
        self._by_entity[record.rp_entity_id] = record
        for uaid in record.cross_listing.listings:
            self._by_uaid[uaid] = record


def _normalize(s: str) -> str:
    return " ".join(s.lower().split())


def record_from_find_companies_response(
    *,
    query: str,
    response_company: dict[str, object],
    primary_uaid: str,
    listings: tuple[str, ...],
    relationships: tuple[tuple[str, ListingType], ...],
    sector: str = "UNKNOWN",
    market_cap_usd: float = 0.0,
) -> EntityRecord:
    return EntityRecord(
        rp_entity_id=str(response_company["rp_entity_id"]),
        canonical_name=str(response_company.get("name", "")),
        primary_uaid=primary_uaid,
        cross_listing=CrossListing(
            rp_entity_id=str(response_company["rp_entity_id"]),
            primary_uaid=primary_uaid,
            listings=listings,
            relationships=relationships,
        ),
        bigdata_search_query=query,
        resolved_at=datetime.now(tz=UTC),
        sector=sector,
        market_cap_usd=market_cap_usd,
    )


def record_for_us_ticker(
    *,
    ticker: str,
    name: str,
    sector: str = "UNKNOWN",
    market_cap_usd: float = 0.0,
    market: str = "XNAS",
    rp_entity_id: str | None = None,
) -> EntityRecord:
    # WHY: free-stack universe build doesn't have RavenPack ids; synthesize a
    # stable per-ticker pseudo-id from the ticker so downstream lookups still work.
    primary_uaid = f"{market}.{ticker.upper()}"
    pseudo_id = rp_entity_id or f"US.{ticker.upper()}"
    return EntityRecord(
        rp_entity_id=pseudo_id,
        canonical_name=name,
        primary_uaid=primary_uaid,
        cross_listing=CrossListing(
            rp_entity_id=pseudo_id,
            primary_uaid=primary_uaid,
            listings=(primary_uaid,),
            relationships=((primary_uaid, "common"),),
        ),
        bigdata_search_query=f"ticker:{ticker.upper()}",
        resolved_at=datetime.now(tz=UTC),
        sector=sector,
        market_cap_usd=market_cap_usd,
    )


def record_to_dict(r: EntityRecord) -> dict[str, object]:
    return {
        **{k: v for k, v in asdict(r).items() if k != "cross_listing"},
        "cross_listing": asdict(r.cross_listing),
        "resolved_at": r.resolved_at.isoformat(),
    }
