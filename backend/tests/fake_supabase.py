"""Atomic SQLite-backed PostgREST test adapter using the checked-in schema.

It exercises real column/check/foreign-key validation and conditional updates,
without connecting to Supabase or substituting a no-op persistence mock.
"""

import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from types import SimpleNamespace


class FakeSupabase:
    def __init__(self, mandate=None):
        self.lock = RLock()
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("CREATE TABLE users(id TEXT PRIMARY KEY)")
        self.connection.execute("INSERT INTO users VALUES ('buyer')")
        sql = (Path(__file__).resolve().parents[2] / "supabase/migrations/001_initial_schema.sql").read_text(encoding="utf-8")
        for name in ("profiles", "payment_tokens", "mandates", "transactions"):
            ddl = re.search(rf"create table public\.{name} \([\s\S]*?\n\);", sql).group(0)
            ddl = (ddl.replace("public.", "").replace("auth.", "")
                   .replace("default uuid_generate_v4()", "default (lower(hex(randomblob(16))))")
                   .replace("default now()", "default CURRENT_TIMESTAMP").replace("::jsonb", ""))
            self.connection.execute(ddl)
        for definition in ("cancel_requested boolean not null default false", "payment_operation text",
                           "payment_operation_id text", "payment_started_at text"):
            self.connection.execute("ALTER TABLE mandates ADD COLUMN " + definition)
        self.table("profiles").insert({"id": "buyer", "email": "buyer@example.com"}).execute()
        self.table("payment_tokens").insert({"id": "db-token", "user_id": "buyer", "paypal_token_id": "vault-id", "is_active": True}).execute()
        if mandate:
            self.table("mandates").insert({"status": "active", **mandate}).execute()

    def table(self, name):
        if name not in ("profiles", "payment_tokens", "mandates", "transactions"):
            raise ValueError("Unknown test table")
        return Query(self, name)


class Query:
    def __init__(self, sb, table):
        self.sb, self.name = sb, table
        self.mode, self.payload, self.filters, self.args = "select", {}, [], []
        self.single, self.columns, self.ordering = False, "*", ""

    def select(self, columns):
        self.columns = columns
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append(f'"{column}" = ?')
        self.args.append(value)
        return self

    def in_(self, column, values):
        self.filters.append(f'"{column}" IN ({",".join("?" for _ in values)})')
        self.args.extend(values)
        return self

    def gt(self, column, value):
        self.filters.append(f'"{column}" > ?')
        self.args.append(value)
        return self

    def lte(self, column, value):
        self.filters.append(f'"{column}" <= ?')
        self.args.append(value)
        return self

    def is_(self, column, value):
        assert value == "null"
        self.filters.append(f'"{column}" IS NULL')
        return self

    def maybe_single(self):
        self.single = True
        return self

    def order(self, column, desc=False):
        self.ordering = f' ORDER BY "{column}" ' + ("DESC" if desc else "ASC")
        return self

    def execute(self):
        names = list(self.payload)
        values = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in self.payload.values()]
        where = " WHERE " + " AND ".join(self.filters) if self.filters else ""
        if self.mode == "insert":
            columns = ",".join('"' + c + '"' for c in names)
            sql = f'INSERT INTO "{self.name}" ({columns}) VALUES ({",".join("?" for _ in values)}) RETURNING *'
        elif self.mode == "update":
            sql = f'UPDATE "{self.name}" SET ' + ",".join(f'"{c}"=?' for c in names) + where + " RETURNING *"
            values.extend(self.args)
        else:
            sql = f'SELECT {self.columns} FROM "{self.name}"' + where + self.ordering
            values = self.args
        with self.sb.lock:
            cursor = self.sb.connection.execute(sql, values)
            rows = [dict(row) for row in cursor.fetchall()]
            self.sb.connection.commit()
        for row in rows:
            for key in ("constraints", "details", "purchase_confirmation_product"):
                if key in row and row[key] is not None:
                    row[key] = json.loads(row[key])
            for key in ("cancel_requested", "is_active", "purchase_confirmation_ready"):
                if key in row:
                    row[key] = bool(row[key])
        return SimpleNamespace(data=(rows[0] if rows else None) if self.single else rows)
