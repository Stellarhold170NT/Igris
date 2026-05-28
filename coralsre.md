# Coral-OpenSRE Integration Guide

> **Coral** là SQL federation engine giúp AI agent snapshot và query đồng thợi nhiều nguồn dữ liệu (Grafana, Datadog, GitHub, Slack, EKS...) bằng một câu SQL duy nhất. Tài liệu này hướng dẫn sử dụng Coral trong OpenSRE.

---

## 1. Kích hoạt Coral

Coral được bật qua biến môi trường trong `.env` hoặc shell:

```bash
# Bật Coral overall
CORAL_ENABLED=true

# (Tuỳ chọn) Đường dẫn binary nếu không có trong PATH
CORAL_BINARY=/usr/local/bin/coral

# (Tuỳ chọn) Tự động bật các source khi integration đã cấu hình
CORAL_AUTO_ENABLE=true
```

### 1.1. Bật từng source riêng lẻ

Thay vì `CORAL_AUTO_ENABLE`, bạn có thể bật từng source cụ thể:

```bash
CORAL_GRAFANA=true
CORAL_DATADOG=true
CORAL_GITHUB=true
CORAL_SLACK=true
CORAL_SENTRY=true
CORAL_GITLAB=true
```

Nếu cả `CORAL_AUTO_ENABLE` và `CORAL_<SOURCE>` đều không bật, Coral vẫn chạy nhưng **không có source nào** ngoài các `@coralapi` custom.

### 1.2. Build Coral binary (một lần)

Coral core là Rust, được build ra binary `coral`. OpenSRE tự động tìm binary theo thứ tự:
1. `CORAL_BINARY` env var
2. `./bin/coral` (trong thư mục gốc OpenSRE)
3. `coral` trong PATH

**Build và copy vào `bin/`:**

```bash
cd coral
cargo build --release -p coral-cli
cp target/release/coral ../bin/coral
```

Sau đó OpenSRE sẽ tự dùng `./bin/coral` mà không cần cài đặt vào hệ thống.

### 1.3. Kiểm tra

```bash
# Kiểm tra coral binary
./bin/coral --version

# Kiểm tra tool trong OpenSRE
uv run opensre chat --help
# Trong interactive chat, tool coral_query sẽ xuất hiện nếu CORAL_ENABLED=true
```

---

## 2. Sử dụng tool `coral_query` (Chat-only)

> **Lưu ý**: `coral_query` chỉ xuất hiện trong **interactive chat** (`opensre chat`), không phải trong **investigation pipeline** (`opensre investigate`). Coral dùng để capture/snapshot trạng thái hệ thống sau khi đã phân tích, không phải để khoan sâu log hay điều tra root cause.

Khi Coral được kích hoạt, AI agent có tool **chỉ trong chat mode** (không dùng trong investigation):

```json
{
  "name": "coral_query",
  "description": "Capture a unified snapshot of system state using Coral SQL..."
}
```

### 2.1. Discovery workflow (khi cần)

Nếu schema chưa rõ, có thể khám phá trước khi snapshot:

```sql
-- 1. Liệt kê tất cả tables
SELECT * FROM coral.tables;

-- 2. Liệt kê functions (table-valued functions)
SELECT * FROM coral.table_functions;

-- 3. Xem cột và filter của một table
SELECT * FROM coral.columns WHERE table_name = 'issues';

-- 4. Xem input/config của một source
SELECT * FROM coral.inputs WHERE schema_name = 'github';
```

### 2.2. Query dữ liệu đơn giản

```sql
-- Grafana dashboards
SELECT title, uid FROM grafana.dashboards LIMIT 10;

-- Datadog metrics
SELECT metric, value FROM datadog.metrics WHERE from='-1h' LIMIT 20;

-- GitHub issues
SELECT title, state, created_at FROM github.issues WHERE state='open' LIMIT 5;
```

### 2.3. JOIN cross-source

Đây là điểm mạnh của Coral — kết hợp dữ liệu từ nhiều hệ thống:

```sql
-- Liên kết GitHub issue với Slack message
SELECT g.title, s.text, s.channel_name
FROM github.issues g
JOIN slack.messages s ON g.title LIKE '%' || s.text || '%'
WHERE g.state = 'open'
LIMIT 10;
```

### 2.4. Gọi Table Function

Một số source có **functions** thay vì tables (cần argument):

```sql
-- GitHub search code
SELECT * FROM github.search_code('repo:myorg/myrepo language:python');
```

---

## 3. Các source native hiện có

| Source | Coral Name | Credentials cần (từ OpenSRE) | Env var bật |
|--------|-----------|------------------------------|-------------|
| Grafana | `grafana` | `endpoint`, `api_key` | `CORAL_GRAFANA` |
| Datadog | `datadog` | `api_key`, `app_key`, `site` | `CORAL_DATADOG` |
| GitHub | `github` | `token` | `CORAL_GITHUB` |
| Slack | `slack` | `token`, `team_id` | `CORAL_SLACK` |
| Sentry | `sentry` | `token`, `organization_slug` | `CORAL_SENTRY` |
| GitLab | `gitlab` | `token`, `base_url` | `CORAL_GITLAB` |

Nếu integration đã được cấu hình trong OpenSRE (qua `~/.config/opensre/integrations.json` hoặc env vars như `GRAFANA_INSTANCE_URL`, `DD_API_KEY`), Coral sẽ tự động map credentials sang đúng format để dùng.

---

## 4. Viết custom source với `@coralapi`

Nếu bạn muốn expose dữ liệu từ một integration **không có** Coral source native (ví dụ: EKS pods, MongoDB stats, internal API...), hãy dùng `@coralapi`.

### 4.1. Cú pháp decorator

```python
from app.coral_api import coralapi, CoralColumn, CoralFilter

@coralapi(
    name="my_source",           # Tên table trong SQL
    description="Mô tả",        # Hiển thị trong catalog
    columns={                   # Schema các cột
        "col_a": "Utf8",        # Shorthand: chỉ cần type string
        "col_b": CoralColumn("Int64", description="Số lần restart"),
    },
    filters={                   # Các filter được phép dùng trong WHERE
        "cluster": CoralFilter(required=True, description="Tên cluster"),
        "namespace": CoralFilter(required=False),
    },
    source="eks",               # Integration gốc (để kiểm tra availability)
    is_available=lambda resolved: "eks" in resolved,
)
def my_source(cluster: str, namespace: str | None = None) -> list[dict]:
    """Trả về list[dict], keys khớp với columns đã khai báo."""
    return [
        {"col_a": "pod-1", "col_b": 0},
        {"col_a": "pod-2", "col_b": 1},
    ]
```

### 4.2. Quy tắc

1. **Tên**: `name` phải unique trong toàn bộ registry.
2. **Return type**: Luôn là `list[dict[str, Any]]`.
3. **Keys**: Các key trong dict phải khớp với tên column đã khai báo.
4. **Filters**: Các parameter của function nên khớp với keys trong `filters`. Coral sẽ tự động map `WHERE cluster='prod'` thành query param `?cluster=prod` gọi đến bridge server.
5. **Lazy import**: Nên import service client **bên trong** function để tránh circular import.

### 4.3. Ví dụ: EKS Pods

```python
# app/coral_api/sources/__init__.py  (hoặc file riêng)

@coralapi(
    name="opensre_pods",
    description="Kubernetes pod status từ EKS clusters",
    columns={
        "pod_name": CoralColumn("Utf8"),
        "namespace": CoralColumn("Utf8"),
        "status": CoralColumn("Utf8"),
        "restarts": CoralColumn("Int64"),
        "node_name": CoralColumn("Utf8"),
    },
    filters={
        "cluster": CoralFilter(required=True),
        "namespace": CoralFilter(required=False),
    },
    source="eks",
    is_available=lambda resolved: "eks" in resolved,
)
def opensre_pods(cluster: str, namespace: str | None = None) -> list[dict]:
    from app.services.eks.eks_client import EKSClient

    client = EKSClient.from_integration(cluster=cluster)
    pods = client.list_pods(cluster=cluster, namespace=namespace)
    return [
        {
            "pod_name": p["name"],
            "namespace": p["namespace"],
            "status": p["status"],
            "restarts": p.get("restarts", 0),
            "node_name": p.get("node", ""),
        }
        for p in pods
    ]
```

Sau khi định nghĩa, bạn có thể query:

```sql
SELECT pod_name, status, restarts
FROM opensre_pods
WHERE cluster = 'prod' AND namespace = 'default'
LIMIT 20;
```

---

## 5. Kiến trúc tổng quan

```
┌─────────────────────────────────────────────────────────────┐
│                        OpenSRE Agent                         │
│  ┌─────────────┐    ┌──────────────────────────────────┐   │
│  │ coral_query │───▶│ CoralManager                     │   │
│  │  (tool)     │    │ ┌──────────────────────────────┐ │   │
│  └─────────────┘    │ │ 1. Source Setup              │ │   │
│                     │ │    coral source add <name>   │ │   │
│  ┌─────────────┐    │ └──────────────────────────────┘ │   │
│  │ @coralapi   │    │ ┌──────────────────────────────┐ │   │
│  │ functions   │───▶│ │ 2. Bridge Server             │ │   │
│  │             │    │ │    localhost:<random_port>   │ │   │
│  └─────────────┘    │ └──────────────────────────────┘ │   │
│                     │ ┌──────────────────────────────┐ │   │
│  ┌─────────────┐    │ │ 3. Query Execution           │ │   │
│  │ Integration │───▶│ │    coral sql --format json   │ │   │
│  │ configs     │    │ └──────────────────────────────┘ │   │
│  └─────────────┘    └──────────────────────────────────┘   │
└──────────────────────────────┬─────────────────────────────┘
                               │
          ┌────────────────────┘
          ▼
┌─────────────────────────┐      ┌──────────────────────────┐
│ Coral CLI Subprocess     │      │ Bridge HTTP Server        │
│ (coral source add,       │      │ (stdlib ThreadingHTTPServer│
│  coral sql)              │      │ Exposes @coralapi funcs)  │
└─────────────────────────┘      └──────────────────────────┘
```

### Luồng khi chạy query đầu tiên

1. `CoralManager.ensure_ready()` kiểm tra source nào được bật.
2. Với **native sources** (grafana, datadog...), chạy `coral source add <name>` để install.
3. Với **@coralapi sources**, start `CoralBridgeServer` trên localhost port random, generate YAML manifest, rồi import vào Coral.
4. Chạy `coral sql --format json "<sql>"` và trả kết quả về agent.

---

## 6. Troubleshooting

### `coral_query` không xuất hiện trong tools

- Kiểm tra `CORAL_ENABLED=true`.
- Kiểm tra `coral` binary có trong PATH: `which coral`.
- Kiểm tra `shutil.which("coral")` từ Python.

### Source install thất bại

- Kiểm tra credentials đã đúng chưa (ví dụ: `GRAFANA_INSTANCE_URL`, `GRAFANA_READ_TOKEN`).
- Kiểm tra log warning từ `CoralManager._install_native_source()`.
- Chạy thủ công để xem lỗi: `CORAL_CONFIG_DIR=~/.config/opensre/coral_workspace coral source add grafana`.

### @coralapi function trả về 404

- Kiểm tra function đã được register: `CoralApiRegistry.all()`.
- Kiểm tra tên trong SQL có khớp với `name` trong decorator không.
- Bridge server chỉ chạy khi có ít nhất một `@coralapi` function `available`.

### Query timeout

- Mặc định timeout là 60s. Có thể tăng bằng cách điều chỉnh trong `CoralManager.execute_sql(timeout=120)`.
- Các HTTP source có thể chậm do pagination — dùng `LIMIT` trong SQL.

---

## 7. Phát triển song song với upstream

Coral Rust core nằm trong `opensre/coral/` và **không bị sửa đổi**. Để cập nhật từ upstream:

```bash
cd opensre/coral
git remote add upstream https://github.com/withcoral/coral.git
git fetch upstream
git merge upstream/main
```

Phần Python bridge (`app/coral_api/`) hoàn toàn độc lập, chỉ giao tiếp với Coral qua CLI và HTTP.
