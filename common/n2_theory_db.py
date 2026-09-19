#!/usr/bin/env python3
"""Validate and store four-dimensional N=2 Lagrangian SCFTs in MySQL.

The input JSON schema is the same schema accepted by
anomalies.check_n2_anomalies.  A theory is stored only if all input and gauge
anomaly checks pass and every one-loop gauge beta function vanishes.

The database keeps theory-wide properties in shared tables and realization
data in separate Lagrangian tables.  A placeholder table for non-Lagrangian
realizations makes it possible to attach other descriptions to the same
theory later. Initial imports store only basic properties. Run
``common/n2_theory_db_indices.py`` separately to fill or upgrade indices.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

if __package__:
    from .json_utils import (
        json_text as _json_text,
        optional_json_text as _optional_json_text,
    )
    from .n2_theory_properties import calculate_n2_theory_properties
    from .number_utils import as_nonnegative_fraction, as_nonnegative_int
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from common.json_utils import (
        json_text as _json_text,
        optional_json_text as _optional_json_text,
    )
    from common.n2_theory_properties import calculate_n2_theory_properties
    from common.number_utils import as_nonnegative_fraction, as_nonnegative_int

from anomalies.check_n2_anomalies import (
    GaugeFactorData,
    HyperData,
    ProductHyperData,
    check_input_data,
)
from anomalies.lie_algebra import conjugate_dynkin_labels


SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    metadata_key VARCHAR(64) NOT NULL,
    metadata_value VARCHAR(255) NOT NULL,
    PRIMARY KEY (metadata_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS theories (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    canonical_key VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_theories_canonical_key (canonical_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS theory_properties (
    theory_id BIGINT UNSIGNED NOT NULL,
    flavor_symmetry VARCHAR(255) NOT NULL,
    flavor_rank INT UNSIGNED NOT NULL,
    flavor_dimension BIGINT UNSIGNED NOT NULL,
    conformal_manifold_dimension INT UNSIGNED NULL,
    central_charges_json JSON NULL,
    central_charge_a_decimal DECIMAL(65, 30)
        GENERATED ALWAYS AS (
            CAST(
                JSON_UNQUOTE(
                    JSON_EXTRACT(
                        central_charges_json,
                        '$.a.numerator'
                    )
                ) AS DECIMAL(65, 30)
            )
            / NULLIF(
                CAST(
                    JSON_UNQUOTE(
                        JSON_EXTRACT(
                            central_charges_json,
                            '$.a.denominator'
                        )
                    ) AS DECIMAL(65, 30)
                ),
                0
            )
        ) STORED,
    central_charge_c_decimal DECIMAL(65, 30)
        GENERATED ALWAYS AS (
            CAST(
                JSON_UNQUOTE(
                    JSON_EXTRACT(
                        central_charges_json,
                        '$.c.numerator'
                    )
                ) AS DECIMAL(65, 30)
            )
            / NULLIF(
                CAST(
                    JSON_UNQUOTE(
                        JSON_EXTRACT(
                            central_charges_json,
                            '$.c.denominator'
                        )
                    ) AS DECIMAL(65, 30)
                ),
                0
            )
        ) STORED,
    coulomb_branch_index_json JSON NULL,
    coulomb_branch_spectrum_json JSON NULL,
    superconformal_index_json JSON NULL,
    superconformal_index_order BIGINT UNSIGNED NULL,
    coulomb_branch_index_max_dimension_json JSON NULL,
    disconnected_sector_count INT UNSIGNED NULL,
    disconnected_sectors_json JSON NULL,
    properties_json JSON NOT NULL,
    PRIMARY KEY (theory_id),
    KEY idx_theory_properties_central_charge_a (
        central_charge_a_decimal
    ),
    KEY idx_theory_properties_central_charge_c (
        central_charge_c_decimal
    ),
    CONSTRAINT fk_theory_properties_theory
        FOREIGN KEY (theory_id) REFERENCES theories(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lagrangian_realizations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    theory_id BIGINT UNSIGNED NOT NULL,
    canonical_hash CHAR(64) NOT NULL,
    gauge_group VARCHAR(255) NOT NULL,
    gauge_factor_count INT UNSIGNED NOT NULL,
    perturbative_gauge_anomaly_free BOOLEAN NOT NULL,
    global_gauge_anomaly_free BOOLEAN NOT NULL,
    anomaly_free BOOLEAN NOT NULL,
    one_loop_beta_vanishes BOOLEAN NOT NULL,
    lagrangian_scft_candidate BOOLEAN NOT NULL,
    input_json JSON NOT NULL,
    anomaly_result_json JSON NOT NULL,
    exactly_marginal_couplings_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_lagrangian_realizations_hash (canonical_hash),
    KEY idx_lagrangian_realizations_theory (theory_id),
    CONSTRAINT fk_lagrangian_realizations_theory
        FOREIGN KEY (theory_id) REFERENCES theories(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS non_lagrangian_realizations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    theory_id BIGINT UNSIGNED NOT NULL,
    construction_type VARCHAR(255) NOT NULL,
    source_reference TEXT NULL,
    data_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_non_lagrangian_realizations_theory (theory_id),
    CONSTRAINT fk_non_lagrangian_realizations_theory
        FOREIGN KEY (theory_id) REFERENCES theories(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS gauge_factors (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    lagrangian_realization_id BIGINT UNSIGNED NOT NULL,
    factor_order INT UNSIGNED NOT NULL,
    factor_key VARCHAR(255) NOT NULL,
    group_name VARCHAR(255) NOT NULL,
    cartan_type VARCHAR(16) NOT NULL,
    lie_rank INT UNSIGNED NOT NULL,
    vector_beta_numerator DECIMAL(65, 0) NOT NULL,
    vector_beta_denominator DECIMAL(65, 0) UNSIGNED NOT NULL,
    matter_beta_numerator DECIMAL(65, 0) NOT NULL,
    matter_beta_denominator DECIMAL(65, 0) UNSIGNED NOT NULL,
    b0_numerator DECIMAL(65, 0) NOT NULL,
    b0_denominator DECIMAL(65, 0) UNSIGNED NOT NULL,
    one_loop_beta_vanishes BOOLEAN NOT NULL,
    witten_anomaly_parity TINYINT UNSIGNED NULL,
    global_gauge_anomaly_free BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_gauge_factors_order (
        lagrangian_realization_id, factor_order
    ),
    UNIQUE KEY uq_gauge_factors_key (
        lagrangian_realization_id, factor_key
    ),
    CONSTRAINT fk_gauge_factors_realization
        FOREIGN KEY (lagrangian_realization_id)
        REFERENCES lagrangian_realizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS hypermultiplets (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    lagrangian_realization_id BIGINT UNSIGNED NOT NULL,
    hyper_order INT UNSIGNED NOT NULL,
    name VARCHAR(255) NOT NULL,
    kind ENUM('full', 'half') NOT NULL,
    multiplicity BIGINT UNSIGNED NOT NULL,
    total_dimension DECIMAL(65, 0) UNSIGNED NOT NULL,
    reality ENUM('real', 'pseudoreal', 'complex') NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_hypermultiplets_order (
        lagrangian_realization_id, hyper_order
    ),
    CONSTRAINT fk_hypermultiplets_realization
        FOREIGN KEY (lagrangian_realization_id)
        REFERENCES lagrangian_realizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS hypermultiplet_representations (
    hypermultiplet_id BIGINT UNSIGNED NOT NULL,
    gauge_factor_id BIGINT UNSIGNED NOT NULL,
    representation_name VARCHAR(255) NOT NULL,
    dynkin_labels_json JSON NOT NULL,
    dimension DECIMAL(65, 0) UNSIGNED NOT NULL,
    dynkin_index_numerator DECIMAL(65, 0) NOT NULL,
    dynkin_index_denominator DECIMAL(65, 0) UNSIGNED NOT NULL,
    reality ENUM('real', 'pseudoreal', 'complex') NOT NULL,
    beta_contribution_numerator DECIMAL(65, 0) NOT NULL,
    beta_contribution_denominator DECIMAL(65, 0) UNSIGNED NOT NULL,
    PRIMARY KEY (hypermultiplet_id, gauge_factor_id),
    CONSTRAINT fk_hyper_representations_hyper
        FOREIGN KEY (hypermultiplet_id)
        REFERENCES hypermultiplets(id) ON DELETE CASCADE,
    CONSTRAINT fk_hyper_representations_gauge_factor
        FOREIGN KEY (gauge_factor_id)
        REFERENCES gauge_factors(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS flavor_symmetry_factors (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    theory_id BIGINT UNSIGNED NOT NULL,
    factor_order INT UNSIGNED NOT NULL,
    group_name VARCHAR(255) NOT NULL,
    lie_algebra VARCHAR(255) NOT NULL,
    lie_rank INT UNSIGNED NOT NULL,
    dimension BIGINT UNSIGNED NOT NULL,
    representation_reality
        ENUM('real', 'pseudoreal', 'complex') NOT NULL,
    half_hyper_units BIGINT UNSIGNED NOT NULL,
    gauge_representation_json JSON NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_flavor_symmetry_factors_order (theory_id, factor_order),
    CONSTRAINT fk_flavor_symmetry_factors_theory
        FOREIGN KEY (theory_id) REFERENCES theories(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS exactly_marginal_couplings (
    lagrangian_realization_id BIGINT UNSIGNED NOT NULL,
    coupling_order INT UNSIGNED NOT NULL,
    gauge_factor_key VARCHAR(255) NOT NULL,
    PRIMARY KEY (lagrangian_realization_id, coupling_order),
    CONSTRAINT fk_exactly_marginal_couplings_realization
        FOREIGN KEY (lagrangian_realization_id)
        REFERENCES lagrangian_realizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


class TheoryCheckError(ValueError):
    """Raised when an input is not a consistent conformal Lagrangian theory."""


@dataclass(frozen=True)
class StoredTheory:
    """Identifiers returned after a successful database import."""

    theory_id: int
    lagrangian_realization_id: int
    inserted: bool
    name: str
    gauge_group: str
    canonical_hash: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _execute(
    connection: Connection, statement: str, parameters: tuple[Any, ...] = ()
) -> int:
    with connection.cursor() as cursor:
        return cursor.execute(statement, parameters)


def _fetchone(
    connection: Connection, statement: str, parameters: tuple[Any, ...] = ()
) -> dict[str, Any] | None:
    with connection.cursor(DictCursor) as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchone()


def _insert(
    connection: Connection, statement: str, parameters: tuple[Any, ...]
) -> int:
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        if cursor.lastrowid is None:
            raise ArithmeticError("MySQL did not return an inserted row id")
        return int(cursor.lastrowid)


def initialize_database(connection: Connection) -> None:
    """Create the production schema or validate its existing version."""
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            _execute(connection, statement)

    version = _fetchone(
        connection,
        """
        SELECT metadata_value
        FROM schema_metadata
        WHERE metadata_key = %s
        """,
        ("schema_version",),
    )
    if version is None:
        _execute(
            connection,
            """
            INSERT INTO schema_metadata(metadata_key, metadata_value)
            VALUES (%s, %s)
            """,
            ("schema_version", str(SCHEMA_VERSION)),
        )
        return

    if version["metadata_value"] != str(SCHEMA_VERSION):
        raise RuntimeError(
            "unsupported database schema version "
            f"{version['metadata_value']}; expected {SCHEMA_VERSION}"
        )


def connect_database(
    database: str,
    *,
    host: str = "127.0.0.1",
    port: int = 3306,
    user: str = "root",
    password: str = "",
    unix_socket: str | None = None,
    connect_timeout: int = 10,
    initialize_schema: bool = True,
) -> Connection:
    """Open a connection owned by the calling thread/process.

    Set initialize_schema=False only after a coordinator has initialized the
    schema, before starting concurrent workers. Never share this connection.
    """
    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        unix_socket=unix_socket,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=connect_timeout,
    )
    try:
        if initialize_schema:
            initialize_database(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _fraction_parts(value: Fraction | int) -> tuple[int, int]:
    fraction = Fraction(value)
    return fraction.numerator, fraction.denominator


def delete_all_theory_data(connection: Connection) -> int:
    """Delete all theory data atomically, retaining tables and schema metadata.

    The caller must obtain explicit confirmation and authenticate a fresh
    connection first. All realization/property/matter tables reference
    theories through ON DELETE CASCADE. This operation owns its transaction;
    do not pass a connection with pending work. No DDL or cache files change.
    """
    version = _fetchone(
        connection, "SELECT metadata_value FROM schema_metadata WHERE metadata_key = %s",
        ("schema_version",),
    )
    if version is None or version["metadata_value"] != str(SCHEMA_VERSION):
        raise ValueError("The database must use the current N2SCFTDB schema before deletion.")
    connection.begin()
    try:
        with connection.cursor() as cursor:
            deleted = cursor.execute("DELETE FROM theories")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return deleted


def _factor_rows(anomaly_result: dict[str, Any]) -> list[dict[str, Any]]:
    if "gauge_factors" in anomaly_result:
        return anomaly_result["gauge_factors"]
    return [
        {
            "id": "gauge",
            "group": anomaly_result["group"],
            "algebra": anomaly_result["algebra"],
            "rank": anomaly_result["rank"],
            "vector_beta_contribution": anomaly_result[
                "vector_beta_contribution"
            ],
            "matter_beta_contribution": anomaly_result[
                "matter_beta_contribution"
            ],
            "b0": anomaly_result["b0"],
            "one_loop_beta_vanishes": anomaly_result[
                "one_loop_beta_vanishes"
            ],
            "witten_anomaly_parity": anomaly_result["witten_anomaly_parity"],
            "global_gauge_anomaly_free": anomaly_result[
                "global_gauge_anomaly_free"
            ],
        }
    ]


def _canonical_representation(
    factor_rows: list[dict[str, Any]],
    representations: dict[str, Any],
) -> tuple[tuple[int, ...], ...]:
    """Prefer lower-numbered Dynkin nodes under simultaneous conjugation."""
    labels = tuple(
        tuple(representations[factor["id"]].labels) for factor in factor_rows
    )
    conjugate = tuple(
        conjugate_dynkin_labels(factor["algebra"], factor_labels)
        for factor, factor_labels in zip(factor_rows, labels)
    )
    return max(labels, conjugate)


def _canonical_lagrangian_payload(
    anomaly_result: dict[str, Any],
) -> dict[str, Any]:
    """Identify conjugates and pair pseudoreal half hypers into full hypers.

    Pair only after aggregating each complete gauge representation, leaving
    one half hyper when its total half-hyper multiplicity is odd. This keeps
    the previous payload for inputs written entirely in full hypers.
    """
    factor_rows = (anomaly_result["gauge_factors"] if "gauge_factors" in anomaly_result
                   else [{"id": "gauge", "algebra": anomaly_result["algebra"]}])
    aggregated: dict[tuple[str, tuple[tuple[int, ...], ...]], int] = {}
    pseudoreal_units: dict[tuple[tuple[int, ...], ...], int] = {}

    def add_hyper(
        kind: str,
        labels: tuple[tuple[int, ...], ...],
        number: int,
        reality: str,
    ) -> None:
        if reality == "pseudoreal":
            units = number * (2 if kind == "full" else 1)
            pseudoreal_units[labels] = pseudoreal_units.get(labels, 0) + units
        else:
            key = (kind, labels)
            aggregated[key] = aggregated.get(key, 0) + number

    if "gauge_factors" in anomaly_result:
        hypers: list[ProductHyperData] = anomaly_result["hypermultiplets"]
        for hyper in hypers:
            if hyper.number == 0:
                continue
            labels = _canonical_representation(
                factor_rows, hyper.representations
            )
            add_hyper(hyper.kind, labels, hyper.number, hyper.reality)
    else:
        hypers: list[HyperData] = anomaly_result["hypermultiplets"]
        algebra = factor_rows[0]["algebra"]
        for hyper in hypers:
            if hyper.number == 0:
                continue
            labels = tuple(hyper.representation.labels)
            conjugate = conjugate_dynkin_labels(algebra, labels)
            add_hyper(
                hyper.kind, (max(labels, conjugate),), hyper.number,
                hyper.representation.reality,
            )

    for labels, units in pseudoreal_units.items():
        full, half = divmod(units, 2)
        if full:
            aggregated[("full", labels)] = full
        if half:
            aggregated[("half", labels)] = half

    canonical_hypers = [
        {
            "kind": kind,
            "dynkin_labels": [list(labels) for labels in representation],
            "number": number,
        }
        for (kind, representation), number in sorted(aggregated.items())
    ]
    return {
        "gauge_algebras": [factor["algebra"] for factor in factor_rows],
        "hypermultiplets": canonical_hypers,
    }


def _canonical_hash(anomaly_result: dict[str, Any]) -> str:
    payload = _json_text(
        _canonical_lagrangian_payload(anomaly_result), canonical=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def find_superconformal_index(
    connection: Connection,
    factors: tuple[GaugeFactorData, ...],
    hypermultiplets: list[HyperData | ProductHyperData],
    *,
    order: int,
) -> str | None:
    """Read a sufficiently precise index for validated internal theory data.

    Match the existing canonical Lagrangian identity (factor IDs are ignored,
    factor order is retained). SQL NULL, JSON null and unknown/lower cutoffs
    are cache misses. This lookup neither initializes the schema nor changes
    data or the caller's transaction. Pure free sectors have no stored gauge
    realization and are calculated directly.
    """
    order = as_nonnegative_int(order, "order")
    if not factors:
        return None
    if len(factors) == 1 and all(isinstance(h, HyperData) for h in hypermultiplets):
        anomaly_result = {"algebra": factors[0].algebra.cartan_type,
                          "hypermultiplets": hypermultiplets}
    else:
        anomaly_result = {"gauge_factors": [{"id": f.factor_id, "algebra": f.algebra.cartan_type}
                                            for f in factors],
                          "hypermultiplets": hypermultiplets}
    row = _fetchone(connection, """
        SELECT p.superconformal_index_json
        FROM lagrangian_realizations AS lr
        JOIN theory_properties AS p ON p.theory_id = lr.theory_id
        WHERE lr.canonical_hash = %s
          AND p.superconformal_index_order >= %s
          AND JSON_TYPE(p.superconformal_index_json) = 'STRING'
    """, (_canonical_hash(anomaly_result), order))
    if row is None:
        return None
    return json.loads(row["superconformal_index_json"])


def _shared_flavor_symmetry(flavor: dict[str, Any]) -> dict[str, Any]:
    """Keep physical multiplicities without the realization's full/half split."""
    return {
        **flavor,
        "factors": [
            {
                key: value for key, value in factor.items()
                if key not in {"full_hypermultiplets", "half_hypermultiplets"}
            }
            for factor in flavor["factors"]
        ],
    }


def _shared_properties(properties: dict[str, Any]) -> dict[str, Any]:
    # Sector factor IDs belong to the first stored realization, so they must
    # not participate in physical-property comparisons for other realizations.
    return {
        "flavor_symmetry": _shared_flavor_symmetry(properties["flavor_symmetry"]),
        "conformal_manifold_dimension": properties[
            "conformal_manifold_dimension"
        ],
        "central_charges": properties["central_charges"],
    }


def _store_disconnected_sectors(connection, theory_id, realization_id, properties):
    """Fill sector metadata once, using the first stored realization's IDs."""
    sectors = properties["disconnected_sectors"]
    if sectors is None:
        raise ValueError("cannot store sectors for a non-SCFT theory")
    serialized = _json_text(sectors)
    count = len(sectors)
    return _execute(connection, """
        UPDATE theory_properties
        SET disconnected_sector_count = %s, disconnected_sectors_json = %s,
            properties_json = JSON_SET(properties_json,
                '$.disconnected_sector_count', %s,
                '$.disconnected_sectors', CAST(%s AS JSON))
        WHERE theory_id = %s
          AND (disconnected_sector_count IS NULL OR disconnected_sectors_json IS NULL
               OR JSON_TYPE(disconnected_sectors_json) = 'NULL')
          AND %s = (SELECT MIN(id) FROM lagrangian_realizations WHERE theory_id = %s)
    """, (count, serialized, count, serialized, theory_id, realization_id, theory_id))


def _checked_results(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    anomaly_result = check_input_data(data)
    if anomaly_result["errors"]:
        raise TheoryCheckError(
            "invalid theory input: " + "; ".join(anomaly_result["errors"])
        )
    if not anomaly_result["anomaly_free"]:
        raise TheoryCheckError("the theory is not gauge-anomaly-free")
    if not anomaly_result["one_loop_beta_vanishes"]:
        raise TheoryCheckError(
            "the theory is not conformal: at least one one-loop gauge beta "
            "function does not vanish"
        )
    if not anomaly_result["lagrangian_scft_candidate"]:
        raise TheoryCheckError(
            "the theory does not pass the Lagrangian SCFT-candidate checks"
        )

    properties = calculate_n2_theory_properties(data)
    if not properties["lagrangian_scft_candidate"]:
        raise ArithmeticError(
            "the anomaly checker and property calculator disagree"
        )
    return anomaly_result, properties


def _insert_theory(
    connection: Connection,
    canonical_hash: str,
    name: str,
    theory_id: int | None,
) -> int:
    if theory_id is not None:
        row = _fetchone(
            connection,
            "SELECT id FROM theories WHERE id = %s FOR UPDATE",
            (theory_id,),
        )
        if row is None:
            raise ValueError(f"unknown theory id {theory_id}")
        return theory_id

    return _insert(
        connection,
        """
        INSERT INTO theories(canonical_key, name)
        VALUES (%s, %s)
        """,
        (f"lagrangian:{canonical_hash}", name),
    )


def _insert_shared_properties(
    connection: Connection,
    theory_id: int,
    properties: dict[str, Any],
    *,
    new_theory: bool = False,
) -> None:
    shared = _shared_properties(properties)
    serialized = _json_text(shared, canonical=True)
    normalized_shared = json.loads(serialized)
    # A parent inserted by this transaction cannot already have properties.
    # A locking read for that absent row takes a gap lock under REPEATABLE
    # READ; concurrent fresh IDs can lock the same gap and deadlock on INSERT.
    # Existing theories still need the row lock for comparison and enrichment.
    existing = None
    if not new_theory:
        existing = _fetchone(
            connection,
            "SELECT properties_json FROM theory_properties WHERE theory_id = %s FOR UPDATE",
            (theory_id,),
        )
    if existing is not None:
        existing_properties = existing["properties_json"]
        if isinstance(existing_properties, str):
            existing_properties = json.loads(existing_properties)
        # Compare only basic physical properties. Index availability/precision
        # belongs to the later enrichment phase and must survive attachment.
        comparable_properties = _shared_properties(existing_properties)
        if comparable_properties != normalized_shared:
            raise ValueError(
                f"theory {theory_id} already has different shared properties"
            )
        normalized_existing = {**existing_properties, **normalized_shared}
        if existing_properties != normalized_existing:
            _execute(connection, """
                UPDATE theory_properties SET properties_json = %s
                WHERE theory_id = %s
            """, (_json_text(normalized_existing, canonical=True), theory_id))
        return

    flavor = shared["flavor_symmetry"]
    _execute(
        connection,
        """
        INSERT INTO theory_properties(
            theory_id,
            flavor_symmetry,
            flavor_rank,
            flavor_dimension,
            conformal_manifold_dimension,
            central_charges_json,
            coulomb_branch_index_json,
            coulomb_branch_spectrum_json,
            superconformal_index_json,
            properties_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            theory_id,
            flavor["connected_group"],
            flavor["rank"],
            flavor["dimension"],
            properties["conformal_manifold_dimension"],
            _optional_json_text(properties["central_charges"]),
            None,
            None,
            None,
            serialized,
        ),
    )
    for order, factor in enumerate(flavor["factors"]):
        _execute(
            connection,
            """
            INSERT INTO flavor_symmetry_factors(
                theory_id,
                factor_order,
                group_name,
                lie_algebra,
                lie_rank,
                dimension,
                representation_reality,
                half_hyper_units,
                gauge_representation_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                theory_id,
                order,
                factor["group"],
                factor["lie_algebra"],
                factor["rank"],
                factor["dimension"],
                factor["representation_reality"],
                factor["half_hyper_units"],
                _json_text(factor["gauge_representation"]),
            ),
        )


def _insert_realization(
    connection: Connection,
    theory_id: int,
    canonical_hash: str,
    data: dict[str, Any],
    anomaly_result: dict[str, Any],
    properties: dict[str, Any],
) -> int:
    factor_rows = _factor_rows(anomaly_result)
    realization_id = _insert(
        connection,
        """
        INSERT INTO lagrangian_realizations(
            theory_id,
            canonical_hash,
            gauge_group,
            gauge_factor_count,
            perturbative_gauge_anomaly_free,
            global_gauge_anomaly_free,
            anomaly_free,
            one_loop_beta_vanishes,
            lagrangian_scft_candidate,
            input_json,
            anomaly_result_json,
            exactly_marginal_couplings_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            theory_id,
            canonical_hash,
            anomaly_result["group"],
            len(factor_rows),
            int(anomaly_result["perturbative_gauge_anomaly_free"]),
            int(anomaly_result["global_gauge_anomaly_free"]),
            int(anomaly_result["anomaly_free"]),
            int(anomaly_result["one_loop_beta_vanishes"]),
            int(anomaly_result["lagrangian_scft_candidate"]),
            _json_text(data, canonical=True),
            _json_text(anomaly_result),
            _json_text(properties["exactly_marginal_gauge_couplings"]),
        ),
    )

    gauge_factor_ids: dict[str, int] = {}
    for order, factor in enumerate(factor_rows):
        vector_numerator, vector_denominator = _fraction_parts(
            factor["vector_beta_contribution"]
        )
        matter_numerator, matter_denominator = _fraction_parts(
            factor["matter_beta_contribution"]
        )
        b0_numerator, b0_denominator = _fraction_parts(factor["b0"])
        gauge_factor_id = _insert(
            connection,
            """
            INSERT INTO gauge_factors(
                lagrangian_realization_id,
                factor_order,
                factor_key,
                group_name,
                cartan_type,
                lie_rank,
                vector_beta_numerator,
                vector_beta_denominator,
                matter_beta_numerator,
                matter_beta_denominator,
                b0_numerator,
                b0_denominator,
                one_loop_beta_vanishes,
                witten_anomaly_parity,
                global_gauge_anomaly_free
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                realization_id,
                order,
                factor["id"],
                factor["group"],
                factor["algebra"],
                factor["rank"],
                vector_numerator,
                vector_denominator,
                matter_numerator,
                matter_denominator,
                b0_numerator,
                b0_denominator,
                int(factor["one_loop_beta_vanishes"]),
                factor["witten_anomaly_parity"],
                int(factor["global_gauge_anomaly_free"]),
            ),
        )
        gauge_factor_ids[factor["id"]] = gauge_factor_id

    if "gauge_factors" in anomaly_result:
        product_hypers: list[ProductHyperData] = anomaly_result[
            "hypermultiplets"
        ]
        for order, hyper in enumerate(product_hypers):
            hyper_id = _insert(
                connection,
                """
                INSERT INTO hypermultiplets(
                    lagrangian_realization_id,
                    hyper_order,
                    name,
                    kind,
                    multiplicity,
                    total_dimension,
                    reality
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    realization_id,
                    order,
                    hyper.name,
                    hyper.kind,
                    hyper.number,
                    hyper.dimension,
                    hyper.reality,
                ),
            )
            for factor in factor_rows:
                factor_key = factor["id"]
                _insert_hyper_representation(
                    connection,
                    hyper_id,
                    gauge_factor_ids[factor_key],
                    hyper.representations[factor_key],
                    hyper.beta_contributions[factor_key],
                )
    else:
        simple_hypers: list[HyperData] = anomaly_result["hypermultiplets"]
        gauge_factor_id = gauge_factor_ids["gauge"]
        for order, hyper in enumerate(simple_hypers):
            representation = hyper.representation
            hyper_id = _insert(
                connection,
                """
                INSERT INTO hypermultiplets(
                    lagrangian_realization_id,
                    hyper_order,
                    name,
                    kind,
                    multiplicity,
                    total_dimension,
                    reality
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    realization_id,
                    order,
                    representation.name,
                    hyper.kind,
                    hyper.number,
                    representation.dimension,
                    representation.reality,
                ),
            )
            _insert_hyper_representation(
                connection,
                hyper_id,
                gauge_factor_id,
                representation,
                hyper.beta_contribution,
            )

    for order, factor_key in enumerate(
        properties["exactly_marginal_gauge_couplings"]
    ):
        _execute(
            connection,
            """
            INSERT INTO exactly_marginal_couplings(
                lagrangian_realization_id,
                coupling_order,
                gauge_factor_key
            )
            VALUES (%s, %s, %s)
            """,
            (realization_id, order, factor_key),
        )
    return realization_id


def _insert_hyper_representation(
    connection: Connection,
    hyper_id: int,
    gauge_factor_id: int,
    representation: Any,
    beta_contribution: Fraction,
) -> None:
    index_numerator, index_denominator = _fraction_parts(
        representation.dynkin_index
    )
    beta_numerator, beta_denominator = _fraction_parts(beta_contribution)
    _execute(
        connection,
        """
        INSERT INTO hypermultiplet_representations(
            hypermultiplet_id,
            gauge_factor_id,
            representation_name,
            dynkin_labels_json,
            dimension,
            dynkin_index_numerator,
            dynkin_index_denominator,
            reality,
            beta_contribution_numerator,
            beta_contribution_denominator
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            hyper_id,
            gauge_factor_id,
            representation.name,
            _json_text(list(representation.labels)),
            representation.dimension,
            index_numerator,
            index_denominator,
            representation.reality,
            beta_numerator,
            beta_denominator,
        ),
    )


def _find_stored_realization(connection, canonical_hash, theory_id):
    """Read a committed duplicate, including one inserted by a concurrent client."""
    existing = _fetchone(
        connection,
        """
        SELECT
            lr.id AS realization_id,
            lr.theory_id,
            lr.gauge_group,
            t.name
        FROM lagrangian_realizations AS lr
        JOIN theories AS t ON t.id = lr.theory_id
        WHERE lr.canonical_hash = %s
        """,
        (canonical_hash,),
    )
    if existing is not None:
        if theory_id is not None and theory_id != existing["theory_id"]:
            raise ValueError(
                "this Lagrangian realization is already attached to theory "
                f"{existing['theory_id']}"
            )
        return StoredTheory(
            theory_id=int(existing["theory_id"]),
            lagrangian_realization_id=int(existing["realization_id"]),
            inserted=False,
            name=str(existing["name"]),
            gauge_group=str(existing["gauge_group"]),
            canonical_hash=canonical_hash,
        )
    return None


def store_lagrangian_theory(
    connection: Connection,
    data: dict[str, Any],
    *,
    name: str | None = None,
    theory_id: int | None = None,
    initialize_schema: bool = True,
) -> StoredTheory:
    """Check a theory and atomically store basic properties and its realization.

    Both indices, their cutoffs, and the Coulomb spectrum start as SQL NULL.
    No index or spectrum calculation occurs during insertion or reimport.

    Fresh theories insert their properties directly, avoiding missing-row gap
    locks between independent imports. Existing shared properties remain locked
    while comparing or merging data for an attached realization.

    Reimporting the same normalized Lagrangian realization is idempotent, even
    across concurrent connections. A unique-key race rolls back the losing
    transaction and returns the committed winner with inserted=False. Deadlocks
    and lock timeouts retry the whole rolled-back transaction up to two times.
    Other failures, including ambiguous connection/commit failures, propagate.

    Set theory_id to attach a new realization to an existing shared theory.
    Use initialize_schema=False in workers after serial schema initialization.
    The connection must belong exclusively to this caller, without an active
    caller transaction; schema changes must finish before concurrent imports.
    """
    if initialize_schema:
        initialize_database(connection)
    anomaly_result, properties = _checked_results(data)
    canonical_hash = _canonical_hash(anomaly_result)

    theory_name = (
        name.strip()
        if name is not None and name.strip()
        else f"{anomaly_result['group']} Lagrangian SCFT"
    )
    for attempt in range(3):
        existing = _find_stored_realization(connection, canonical_hash, theory_id)
        if existing is not None:
            return existing
        connection.begin()
        try:
            stored_theory_id = _insert_theory(
                connection, canonical_hash, theory_name, theory_id
            )
            _insert_shared_properties(
                connection, stored_theory_id, properties, new_theory=theory_id is None,
            )
            realization_id = _insert_realization(
                connection, stored_theory_id, canonical_hash, data,
                anomaly_result, properties,
            )
            _store_disconnected_sectors(connection, stored_theory_id, realization_id, properties)
            _execute(
                connection,
                """
                UPDATE theories
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (stored_theory_id,),
            )
            connection.commit()
            break
        except BaseException as exc:
            try:
                connection.rollback()
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{type(exc).__name__}: {exc}; rollback failed: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                ) from exc
            code = exc.args[0] if exc.args else None
            if isinstance(exc, pymysql.IntegrityError) and code == 1062:
                existing = _find_stored_realization(connection, canonical_hash, theory_id)
                if existing is not None:
                    return existing
            if isinstance(exc, pymysql.OperationalError) and code in (1205, 1213) and attempt < 2:
                time.sleep(0.05 * (2 ** attempt))
                continue
            raise

    stored_row = _fetchone(
        connection,
        "SELECT name FROM theories WHERE id = %s",
        (stored_theory_id,),
    )
    if stored_row is None:
        raise ArithmeticError("stored theory disappeared after commit")
    return StoredTheory(
        theory_id=stored_theory_id,
        lagrangian_realization_id=realization_id,
        inserted=True,
        name=str(stored_row["name"]),
        gauge_group=anomaly_result["group"],
        canonical_hash=canonical_hash,
    )


# Column names are fixed here; values always use MySQL parameters.
_INDEX_COLUMNS = {
    "superconformal_index": "superconformal_index_json",
    "superconformal_index_order": "superconformal_index_order",
    "coulomb_branch_index": "coulomb_branch_index_json",
    "coulomb_branch_index_max_dimension": "coulomb_branch_index_max_dimension_json",
    "coulomb_branch_spectrum": "coulomb_branch_spectrum_json",
}
_INDEX_CUTOFFS = {
    "superconformal_index": "superconformal_index_order",
    "coulomb_branch_index": "coulomb_branch_index_max_dimension",
}


def _decode_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _exact_cutoff(value: Any, *, full_index: bool = False) -> int | Fraction:
    if full_index:
        result = as_nonnegative_int(value, "superconformal_index_order")
        if result > 2**64 - 1:
            raise ValueError("superconformal_index_order exceeds MySQL BIGINT UNSIGNED")
        return result
    if isinstance(value, dict):
        if set(value) != {"numerator", "denominator"}:
            raise ValueError("exact cutoff requires numerator and denominator")
        numerator = as_nonnegative_int(value["numerator"], "cutoff numerator")
        denominator = as_nonnegative_int(value["denominator"], "cutoff denominator")
        if denominator == 0:
            raise ValueError("cutoff denominator must be positive")
        value = Fraction(numerator, denominator)
    return as_nonnegative_fraction(value, "coulomb_branch_index_max_dimension")


def _index_state(row: dict[str, Any]) -> dict[str, Any]:
    state = {}
    for key, column in _INDEX_COLUMNS.items():
        value = row[column]
        if key != "superconformal_index_order":
            value = _decode_json(value)
        if value is not None and key in _INDEX_CUTOFFS.values():
            value = _exact_cutoff(value, full_index=key == "superconformal_index_order")
        state[key] = value
    return state


def _index_replacement_reason(value: Any, old_cutoff: Any, new_cutoff: Any) -> str:
    if value is None:
        return "missing"
    if old_cutoff is None:
        return "unknown_precision"
    if new_cutoff > old_cutoff:
        return "higher_order"
    return "equal_order" if new_cutoff == old_cutoff else "lower_order"


def _locked_index_row(connection: Connection, realization_id: int) -> dict[str, Any]:
    # Resolve without child locks, then lock parent first, as imports/deletes do.
    # Locking children first can deadlock against a parent's FK cascade/check.
    owner = _fetchone(connection,
                     "SELECT theory_id FROM lagrangian_realizations WHERE id = %s",
                     (realization_id,))
    if owner is None or _fetchone(
        connection, "SELECT id FROM theories WHERE id = %s FOR UPDATE", (owner["theory_id"],),
    ) is None:
        raise ValueError(f"unknown Lagrangian realization {realization_id}")
    row = _fetchone(connection, """
        SELECT p.*, lr.id AS realization_id
        FROM theory_properties AS p
        JOIN lagrangian_realizations AS lr ON lr.theory_id = p.theory_id
        WHERE lr.id = %s
        FOR UPDATE
    """, (realization_id,))
    if row is None or row["theory_id"] != owner["theory_id"]:
        raise ValueError(f"unknown Lagrangian realization {realization_id}")
    return row


def _lagrangian_index_state(connection, realization_id, *, theory_id):
    """Read one retained realization's index state without scanning or locking."""
    row = _fetchone(connection, """
        SELECT p.* FROM theory_properties AS p
        JOIN lagrangian_realizations AS lr ON lr.theory_id = p.theory_id
        WHERE lr.id = %s AND p.theory_id = %s
    """, (realization_id, theory_id))
    if row is None:
        raise ValueError(f"unknown Lagrangian realization {realization_id} for theory {theory_id}")
    return _index_state(row)


def missing_lagrangian_index_fields(connection, realization_id, *, theory_id):
    """Recheck a retained job by ID without rescanning the database or locking."""
    state = _lagrangian_index_state(connection, realization_id, theory_id=theory_id)
    return [key for key in (*_INDEX_CUTOFFS, "coulomb_branch_spectrum") if state[key] is None]


def needed_lagrangian_index_fields(connection, realization_id, *, theory_id, order, max_dimension):
    """Recheck missing components and known lower cutoffs for one retained job."""
    order = _exact_cutoff(order, full_index=True)
    max_dimension = _exact_cutoff(max_dimension)
    state = _lagrangian_index_state(connection, realization_id, theory_id=theory_id)
    needed = []
    for key, cutoff in (("superconformal_index", order), ("coulomb_branch_index", max_dimension)):
        reason = _index_replacement_reason(state[key], state[_INDEX_CUTOFFS[key]], cutoff)
        if reason in {"missing", "higher_order"}:
            needed.append(key)
    if state["coulomb_branch_spectrum"] is None:
        needed.append("coulomb_branch_spectrum")
    return needed


def _run_index_transaction(connection, action):
    """Retry only rolled-back lock failures, never ambiguous connection errors."""
    for attempt in range(3):
        connection.begin()
        try:
            result = action()
            connection.commit()
            return result
        except BaseException as exc:
            try:
                connection.rollback()
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{type(exc).__name__}: {exc}; rollback failed: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                ) from exc
            code = exc.args[0] if exc.args else None
            if isinstance(exc, pymysql.OperationalError) and code in (1205, 1213) and attempt < 2:
                time.sleep(0.05 * (2 ** attempt))
                continue
            raise


def _write_index_changes(
    connection: Connection, row: dict[str, Any], changes: dict[str, Any]
) -> None:
    if not changes:
        return
    # Read and merge while holding the row lock, preserving basic properties
    # and any independent index update committed during the calculation.
    combined = {**_decode_json(row["properties_json"]), **changes}
    assignments = [f"{_INDEX_COLUMNS[key]} = %s" for key in changes]
    values = [
        value if key == "superconformal_index_order" else _optional_json_text(value)
        for key, value in changes.items()
    ]
    _execute(connection, f"""
        UPDATE theory_properties
        SET {', '.join(assignments)}, properties_json = %s
        WHERE theory_id = %s
    """, (*values, _json_text(combined, canonical=True), row["theory_id"]))
    _execute(connection, """
        UPDATE theories SET updated_at = CURRENT_TIMESTAMP WHERE id = %s
    """, (row["theory_id"],))


def update_lagrangian_indices(
    connection: Connection, realization_id: int, indices: dict[str, Any], *,
    missing_only: bool = False,
) -> dict[str, Any]:
    """Atomically fill or upgrade a realization's shared theory indices.

    Accept the result of ``calculate_n2_theory_indices`` or any subset of its
    three computed fields with their corresponding cutoffs. None/omitted fields
    are left alone. A supplied index requires a nonnegative explicit cutoff;
    never infer precision from the highest nonzero monomial. Equal or lower
    cutoffs keep the stored index. Unknown legacy precision also keeps it;
    use ``record_lagrangian_index_cutoffs`` when its original cutoff is known.
    Spectra are complete: fill missing spectra and reject conflicting ones.

    The connection must have an initialized schema and no caller transaction.
    Calculation belongs outside this short transaction. Row locking makes
    competing workers recheck the latest stored precision before writing.
    ``missing_only`` preserves every present component, including a result
    filled by another worker after a GUI search. Lock failures retry at most
    twice after full rollback, without repeating the calculation.
    """
    unknown = set(indices) - _INDEX_COLUMNS.keys()
    if unknown:
        raise ValueError(f"unknown index fields: {sorted(unknown)}")
    incoming = {}
    for key, cutoff_key in _INDEX_CUTOFFS.items():
        value = indices.get(key)
        cutoff = indices.get(cutoff_key)
        if value is None:
            if cutoff is not None:
                raise ValueError(f"{cutoff_key} requires {key}")
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a nonempty serialized index string")
        if cutoff is None:
            raise ValueError(f"{key} requires {cutoff_key}")
        incoming[key] = value
        incoming[cutoff_key] = _exact_cutoff(
            cutoff, full_index=key == "superconformal_index"
        )
    spectrum = indices.get("coulomb_branch_spectrum")
    if spectrum is not None:
        if not isinstance(spectrum, (list, tuple)):
            raise ValueError("coulomb_branch_spectrum must be a sequence of dimensions")
        spectrum = tuple(sorted(_exact_cutoff(value) for value in spectrum))
        if any(value <= 0 for value in spectrum):
            raise ValueError("Coulomb generator dimensions must be positive")
        incoming["coulomb_branch_spectrum"] = spectrum

    def save():
        row = _locked_index_row(connection, realization_id)
        state = _index_state(row)
        changes, skipped = {}, {}
        for key, cutoff_key in _INDEX_CUTOFFS.items():
            if key not in incoming:
                continue
            if missing_only and state[key] is not None:
                skipped[key] = "already_present"
                continue
            reason = _index_replacement_reason(
                state[key], state[cutoff_key], incoming[cutoff_key]
            )
            if reason in ("missing", "higher_order"):
                changes[key] = incoming[key]
                changes[cutoff_key] = incoming[cutoff_key]
            else:
                skipped[key] = reason
        if "coulomb_branch_spectrum" in incoming:
            previous = state["coulomb_branch_spectrum"]
            if previous is None:
                changes["coulomb_branch_spectrum"] = spectrum
            elif not missing_only and tuple(sorted(_exact_cutoff(value) for value in previous)) != spectrum:
                raise ValueError("stored Coulomb spectrum conflicts with the supplied spectrum")
            else:
                skipped["coulomb_branch_spectrum"] = "already_present"
        _write_index_changes(connection, row, changes)
        return {
            "theory_id": int(row["theory_id"]),
            "lagrangian_realization_id": realization_id,
            "updated_fields": list(changes), "skipped_fields": skipped,
        }
    return _run_index_transaction(connection, save)


def record_lagrangian_index_cutoffs(
    connection: Connection, realization_id: int, *,
    order: Any | None = None, max_dimension: Any | None = None,
) -> None:
    """Record known original cutoffs for legacy indices, without recomputation.

    Supply cutoffs from the original calculation, not its highest nonzero term.
    Existing known cutoffs cannot be changed. The initialized connection must
    have no caller transaction. Both metadata columns and combined JSON update
    atomically, allowing subsequent normal upgrades of legacy results.
    """
    requested = {}
    if order is not None:
        requested["superconformal_index"] = _exact_cutoff(order, full_index=True)
    if max_dimension is not None:
        requested["coulomb_branch_index"] = _exact_cutoff(max_dimension)
    if not requested:
        raise ValueError("supply at least one known cutoff")
    def save():
        row = _locked_index_row(connection, realization_id)
        state = _index_state(row)
        changes = {}
        for key, cutoff in requested.items():
            cutoff_key = _INDEX_CUTOFFS[key]
            if state[key] is None:
                raise ValueError(f"cannot record precision for missing {key}")
            if state[cutoff_key] is None:
                changes[cutoff_key] = cutoff
            elif state[cutoff_key] != cutoff:
                raise ValueError(f"{key} already has a different known cutoff")
        _write_index_changes(connection, row, changes)
    _run_index_transaction(connection, save)


def iter_lagrangian_index_jobs(
    connection: Connection, *, order: Any, max_dimension: Any,
    upgrade: bool = False, batch_size: int = 100,
):
    """Stream one realization per theory needing index work, using bounded pages.

    Default selection fills any missing index or spectrum. With ``upgrade``,
    also select indices with known lower cutoffs. Legacy unknown precision is
    reported in ``unknown_precision`` and left alone. Complete theories with
    unknown precision are reported only in upgrade mode. Reads and calculations
    hold no database row locks; the update API rechecks all write decisions.
    """
    order = _exact_cutoff(order, full_index=True)
    max_dimension = _exact_cutoff(max_dimension)
    batch_size = as_nonnegative_int(batch_size, "batch_size")
    if not batch_size:
        raise ValueError("batch_size must be positive")
    after_id = 0
    while True:
        with connection.cursor(DictCursor) as cursor:
            cursor.execute("""
                SELECT p.*, lr.id AS realization_id, lr.input_json
                FROM lagrangian_realizations AS lr
                JOIN theory_properties AS p ON p.theory_id = lr.theory_id
                WHERE lr.id > %s
                  AND lr.id = (
                      SELECT MIN(other.id) FROM lagrangian_realizations AS other
                      WHERE other.theory_id = lr.theory_id
                  )
                  AND (%s OR p.superconformal_index_json IS NULL
                      OR JSON_TYPE(p.superconformal_index_json) = 'NULL'
                      OR p.coulomb_branch_index_json IS NULL
                      OR JSON_TYPE(p.coulomb_branch_index_json) = 'NULL'
                      OR p.coulomb_branch_spectrum_json IS NULL
                      OR JSON_TYPE(p.coulomb_branch_spectrum_json) = 'NULL')
                ORDER BY lr.id LIMIT %s
            """, (after_id, upgrade, batch_size))
            rows = cursor.fetchall()
        if not rows:
            return
        for row in rows:
            after_id = int(row["realization_id"])
            state = _index_state(row)
            needed, unknown = [], []
            for key, cutoff in (
                ("superconformal_index", order),
                ("coulomb_branch_index", max_dimension),
            ):
                reason = _index_replacement_reason(state[key], state[_INDEX_CUTOFFS[key]], cutoff)
                if reason == "missing" or (upgrade and reason == "higher_order"):
                    needed.append(key)
                elif reason == "unknown_precision":
                    unknown.append(key)
            if state["coulomb_branch_spectrum"] is None:
                needed.append("coulomb_branch_spectrum")
            if needed or (upgrade and unknown):
                yield {
                    "theory_id": int(row["theory_id"]),
                    "lagrangian_realization_id": after_id,
                    "input": _decode_json(row["input_json"]),
                    "needed_fields": needed, "unknown_precision": unknown,
                }


def store_lagrangian_theory_from_file(
    database: str,
    input_path: str | Path,
    *,
    name: str | None = None,
    theory_id: int | None = None,
    host: str = "127.0.0.1",
    port: int = 3306,
    user: str = "root",
    password: str = "",
    unix_socket: str | None = None,
    connect_timeout: int = 10,
) -> StoredTheory:
    """Load a JSON file and store a passing theory in MySQL."""
    with Path(input_path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    connection = connect_database(
        database,
        host=host,
        port=port,
        user=user,
        password=password,
        unix_socket=unix_socket,
        connect_timeout=connect_timeout,
    )
    try:
        return store_lagrangian_theory(
            connection, data, name=name, theory_id=theory_id
        )
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="anomaly-checker JSON file")
    parser.add_argument("database", help="name of an existing MySQL database")
    parser.add_argument("--name", help="display name for a newly created theory")
    parser.add_argument(
        "--host",
        default=os.environ.get("N2_DB_HOST", "127.0.0.1"),
        help="MySQL server host (default: N2_DB_HOST or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3306,
        help="MySQL server port (default: 3306)",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("N2_DB_USER", "root"),
        help="MySQL user (default: N2_DB_USER or root)",
    )
    parser.add_argument(
        "--unix-socket",
        default=os.environ.get("N2_DB_UNIX_SOCKET"),
        help="optional MySQL Unix socket path",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=10,
        help="connection timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--theory-id",
        type=int,
        help="attach the realization to an existing theory id",
    )
    args = parser.parse_args(argv)
    try:
        result = store_lagrangian_theory_from_file(
            args.database,
            args.input,
            name=args.name,
            theory_id=args.theory_id,
            host=args.host,
            port=args.port,
            user=args.user,
            password=os.environ.get("N2_DB_PASSWORD", ""),
            unix_socket=args.unix_socket,
            connect_timeout=args.connect_timeout,
        )
    except TheoryCheckError as exc:
        print(f"Rejected: {exc}", file=sys.stderr)
        return 1
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
        ArithmeticError,
        pymysql.MySQLError,
        RuntimeError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
