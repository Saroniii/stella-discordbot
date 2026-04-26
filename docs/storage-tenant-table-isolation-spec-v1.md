# ストレージ完全テナント分離仕様書 v1

## 1. 目的
- `root` と各 `guild` のデータを**物理テーブル単位で完全分離**する。
- 対象は「コンフィグ / Webhook / 各種ログ / レベル関連 / Tick関連」を含む永続データ全体。
- 既存 CLI の操作語彙（`switch root`, `switch guild` など）は変更しない。

## 2. 適用範囲
### In Scope
- `configs`
- `audit_logs`
- `system_logs`
- `crash_logs`
- `utility_webhooks`
- `level_users`
- `level_runtime`
- `level_event_logs`（有効時）
- `tick_usage_minute`（永続している場合）

### Out of Scope
- Discord 側キャッシュ（ライブラリ内キャッシュ）
- オンメモリ一時状態（CLIセッション、短期メッセージキャッシュ等）
- 機能仕様変更（sticky/level/guild-log の業務ロジック自体）

## 3. テナント定義
- `root` テナント: `tenant_key = r0`
- `guild` テナント: `tenant_key = g<guild_id>`
- 物理テーブル名:
  - `t_<tenant_key>__<logical_table>`
  - 例: `t_g1164522747930628158__configs`
  - 例: `t_r0__system_logs`

## 4. テーブル設計方針
- **各テナントごとに同一スキーマのテーブルセットを作成**する。
- `scope_type/scope_id` はルーティングにのみ使用し、テナントテーブル内部でのフィルタ用途では使わない。
- ID はテナント内で一意（テナント間で重複可）。

## 5. テナントレジストリ（共有メタ）
- 共有テーブル（最小）:
  - `tenant_registry(tenant_key PK, scope_type, scope_id, created_at, last_seen_at, schema_version, migration_phase)`
- 用途:
  - 既存テナント検出
  - DDL適用対象の列挙
  - 移行進捗管理

## 6. Storage API の必須変更
`utils/storage.py` は直接固定テーブルを叩かず、必ずルータ経由にする。

必須内部API:
1. `resolve_tenant_key(scope_type, scope_id) -> str`
2. `ensure_tenant_registered(scope_type, scope_id) -> tenant_key`
3. `ensure_tenant_tables(tenant_key) -> None`
4. `table_name(tenant_key, logical_name) -> str`

既存公開APIは互換維持しつつ、内部で tenant table にルーティングする。

## 7. 起動時 bind フロー
1. `tenant_registry` を読み込み
2. `root` を先に `ensure_tenant_tables`
3. guild を順次 `ensure_tenant_tables`
4. `root` bind → 各 guild bind

ログ要件:
- 各テナントの `system_logs` に `bind-started` / `bind-completed` / `bind-failed`
- `root` の `system_logs` に全体サマリ

## 8. マイグレーション戦略（段階移行）
### Phase 0: 事前導入
- tenant router 実装
- tenant table DDL（idempotent）
- registry導入

### Phase 1: Dual Write
- 読み取り: 旧共有テーブル
- 書き込み: 旧共有 + 新テナントテーブルの二重化

### Phase 2: Backfill
- `root` と各 `guild` を順に backfill
- 件数・ハッシュ比較（サンプリング可）

### Phase 3: Read Cutover
- 読み取りをテナントテーブルへ切替
- 1リリースは dual write 継続

### Phase 4: Legacy Freeze / Cleanup
- 旧共有テーブル書込停止
- 退避期間後に削除

## 9. 障害時方針
- 1テナントの DDL / bind 失敗は他テナントへ波及させない。
- migration 失敗テナントは旧読み取りを維持（段階ロールバック可能）。
- クロステナント一括トランザクションは禁止。

## 10. セキュリティ方針
- テーブル名は内部生成のみ（ユーザー入力不可）。
- SQL構築で `logical_name` は固定ホワイトリストから選択。
- `switch root/guild` は従来の権限モデルを維持。

## 11. 可観測性
`system log detail_json` に追加:
- `tenant_key`
- `physical_table`
- `migration_phase`
- `read_source`（legacy|tenant）

補足:
- migration/bind補助ログの tick課金は従来ポリシーに合わせて exempt 扱い可能（実装で統一）。

## 12. 受け入れ基準
1. `root` と各 `guild` で対象全ドメインが物理テーブル分離されている。
2. 切替後、対象ドメインで共有テーブル参照が残っていない。
3. `switch root` / `switch guild` で他テナントデータが混在しない。
4. 1テナント障害時も他テナントは bind / 動作継続。
5. migration 検証（件数整合・主要レコード整合）に合格。
6. `PYTHONPATH=. pytest -q` 全通過。

## 13. テスト計画（最低限）
### Unit
- tenant key 解決
- table name 生成
- DDL idempotency

### Integration
- guild A/B 分離検証
- root/guild 分離検証
- bind 部分失敗の隔離検証

### Migration
- dual write 整合
- backfill 整合
- read cutover 後の回帰

## 14. 実装対象ファイル（一次）
- `utils/storage.py`
- `utils/config_bind.py`
- `tests/test_storage.py`
- `tests/test_config_bind.py`
- 必要に応じて `tests/test_engine.py`（ログ/参照経路の回帰）

## 15. 備考
- 現行の「1テーブル内で guild をフィルタする」方式は最終的に廃止。
- ログIDの飛び/順序混在問題は、テナント分離後に「同一テナント内の時系列」で追跡しやすくなる。
