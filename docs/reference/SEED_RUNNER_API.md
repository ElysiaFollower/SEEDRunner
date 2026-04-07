# seed-runner API 规范 v2

## 概述

`seed-runner` 是 SEEDRunner 项目的核心工具，为 Agent 提供与远程 VM 交互的统一接口。

**设计原则**：
- 隐藏 SSH、tmux、sshfs 的复杂性
- 提供最小化的命令集
- 挂载管理和 session 管理分离
- 所有输出自动写入共享文件夹，Agent 通过本地文件读取
- Agent 使用相对路径操作，对远程文件系统无感知

---

## 架构

```
Agent (本地)
    ↓ (调用 seed-runner CLI)
seed-runner (本地工具)
    ├─ 挂载管理层
    │  ├─ 管理 sshfs 挂载
    │  └─ 维护挂载元数据
    ├─ Session 管理层
    │  ├─ 管理 tmux session
    │  ├─ 处理 SSH 命令转义
    │  └─ 写入日志文件
    └─ 日志管理层
       └─ 按 session name 组织日志
    ↓ (SSH + tmux)
远程 VM
    ├─ 执行命令
    ├─ 生成输出
    └─ 写入文件系统
    ↓ (远端通过 sshfs 挂载本地共享目录)
本地共享目录
    ├─ logs/
    │  ├─ exp-web-01/
    │  │  ├─ cmd_001.log
    │  │  ├─ cmd_002.log
    │  │  └─ ...
    │  └─ exp-crypto-02/
    │     ├─ cmd_001.log
    │     └─ ...
    └─ artifacts/
       ├─ code/
       ├─ results/
       └─ ...
```

---

## 命令集

### 第一层：挂载管理

#### 1.1 创建挂载

**命令**：
```bash
seed-runner mount create \
  --machine <machine-id> \
  --local-dir <local-mount-point> \
  [--remote-dir <remote-experiment-dir>] \
  [--timeout <seconds>]
```

**参数**：
- `--machine` (必需) — 目标机器的标识符，对应 SSH 配置中的 host
- `--local-dir` (必需) — 本地共享目录路径（将被远程 VM 通过 sshfs 挂载）
- `--remote-dir` (可选) — 远程 VM 中的实验目录路径，默认 `~/seed-experiment`
- `--timeout` (可选) — 挂载操作的超时时间，单位秒，默认 30

**返回值**（JSON）：
```json
{
  "mount_id": "mnt_20260407_001",
  "machine": "vm-seed-01",
  "local_path": "/Users/ely/workspace/research/agent/SEEDRunner/artifacts",
  "remote_path": "/home/user/seed-experiment",
  "status": "mounted",
  "mounted_at": "2026-04-07T10:30:00Z"
}
```

**行为**：
1. 验证 SSH 连接到目标机器
2. 在远程 VM 中创建实验目录（如果不存在）
3. 在本地创建共享目录（如果不存在）
4. 在远程 VM 中执行 sshfs，将本地目录挂载到远程实验目录
5. 返回 mount_id 供后续使用

**错误处理**：
- SSH 连接失败 → 返回错误信息
- 远程目录创建失败 → 返回错误信息
- sshfs 挂载失败 → 返回错误信息，清理已创建的目录

---

#### 1.2 查询挂载状态

**命令**：
```bash
seed-runner mount status --mount-id <mount-id>
```

**参数**：
- `--mount-id` (必需) — mount_id

**返回值**（JSON）：
```json
{
  "mount_id": "mnt_20260407_001",
  "machine": "vm-seed-01",
  "local_path": "/Users/ely/workspace/research/agent/SEEDRunner/artifacts",
  "remote_path": "/home/user/seed-experiment",
  "status": "mounted",
  "mounted_at": "2026-04-07T10:30:00Z",
  "session_count": 3
}
```

**状态值**：
- `mounted` — 挂载正常
- `unmounted` — 已卸载
- `error` — 挂载出现错误

---

#### 1.3 销毁挂载

**命令**：
```bash
seed-runner mount destroy --mount-id <mount-id> [--cleanup]
```

**参数**：
- `--mount-id` (必需) — mount_id
- `--cleanup` (可选) — 是否清理远程 VM 中的实验目录，默认 false

**返回值**（JSON）：
```json
{
  "mount_id": "mnt_20260407_001",
  "status": "unmounted",
  "unmounted_at": "2026-04-07T10:31:00Z",
  "artifacts_preserved": true,
  "artifacts_location": "/Users/ely/workspace/research/agent/SEEDRunner/artifacts"
}
```

**行为**：
1. 卸载 sshfs 挂载
2. 保留本地日志和产物（便于事后审计）
3. 如果指定 `--cleanup`，删除远程 VM 中的实验目录

**错误处理**：
- Mount 不存在 → 返回错误
- 卸载失败 → 返回警告，但继续清理

---

### 第二层：Session 管理

#### 2.1 创建 Session

**命令**：
```bash
seed-runner session create \
  --machine <machine-id> \
  --mount-id <mount-id> \
  --name <session-name> \
  [--timeout <seconds>]
```

**参数**：
- `--machine` (必需) — 目标机器的标识符
- `--mount-id` (必需) — 由 `mount create` 返回的 mount_id
- `--name` (必需) — Session 的可读名称，用于日志分组（如 `exp-web-01`）
- `--timeout` (可选) — 整个 session 的超时时间，单位秒，默认 3600

**返回值**（JSON）：
```json
{
  "session_id": "sess_20260407_001",
  "session_name": "exp-web-01",
  "machine": "vm-seed-01",
  "mount_id": "mnt_20260407_001",
  "local_mount_point": "/Users/ely/workspace/research/agent/SEEDRunner/artifacts",
  "remote_work_dir": "/home/user/seed-experiment",
  "status": "ready",
  "tmux_session": "seed_sess_20260407_001",
  "created_at": "2026-04-07T10:30:00Z"
}
```

**行为**：
1. 验证 mount 存在且状态为 mounted
2. 在远程 VM 中创建 tmux session
3. 在 tmux session 中自动执行 `cd <remote_work_dir>`（设置初始工作目录）
4. 在本地创建日志目录 `<local_mount_point>/logs/<session-name>/`
5. 返回 session_id 供后续命令使用

**关键设计**：
- Session 创建时自动进入挂载的远程路径
- Agent 之后使用相对路径操作，对远程文件系统无感知
- 日志按 session name 分组，便于查找和审计

**错误处理**：
- Mount 不存在 → 返回错误
- Mount 状态异常 → 返回错误
- tmux 创建失败 → 返回错误

---

#### 2.2 执行命令

**命令**：
```bash
seed-runner session exec \
  --session <session-id> \
  --cmd "<shell-command>" \
  [--timeout <seconds>]
```

**参数**：
- `--session` (必需) — 由 `session create` 返回的 session_id
- `--cmd` (必需) — 要执行的 shell 命令（原始字符串，不需要转义）
- `--timeout` (可选) — 单条命令的超时时间，单位秒，默认 300

**返回值**（JSON）：
```json
{
  "session_id": "sess_20260407_001",
  "session_name": "exp-web-01",
  "command": "cd code && make",
  "exit_code": 0,
  "log_file_local": "/Users/ely/workspace/research/agent/SEEDRunner/artifacts/logs/exp-web-01/cmd_002.log",
  "log_file_remote": "/home/user/seed-experiment/logs/exp-web-01/cmd_002.log",
  "log_filename": "cmd_002.log",
  "executed_at": "2026-04-07T10:30:15Z",
  "duration_ms": 2345
}
```

**行为**：
1. 验证 session 存在且状态为 active
2. 在 tmux session 中执行命令（整体执行，不拆分）
3. 捕获 stdout、stderr、exit code
4. 将输出写入 `<remote_work_dir>/logs/<session-name>/<log-filename>`
5. 日志通过共享目录自动同步到本地（底层由远程 VM 挂载本地目录实现）
6. 返回执行结果（包含本地和远程路径）

**日志文件命名**：
- 自动递增：`cmd_001.log`, `cmd_002.log`, ...
- 不需要 Agent 指定
- 便于按执行顺序排序

**日志文件格式**：
```
[2026-04-07T10:30:15Z] $ cd code && make
[2026-04-07T10:30:15Z] Entering directory '/home/user/seed-experiment/code'
[2026-04-07T10:30:17Z] gcc -o test test.c
[2026-04-07T10:30:18Z] $ exit_code: 0
```

**字符转义处理**：
- `seed-runner session exec` 内部处理所有 shell 转义
- Agent 传入原始命令字符串，无需手动转义
- 支持的特殊字符：`$`, `"`, `'`, `\`, `|`, `&`, `;`, `>`, `<`, 等

**错误处理**：
- Session 不存在 → 返回错误
- Session 已销毁 → 返回错误
- 命令执行超时 → 返回超时错误，但 session 保持 active（可继续执行）
- 命令执行失败（exit code != 0） → 返回失败状态，session 保持 active

---

#### 2.3 查询 Session 状态

**命令**：
```bash
seed-runner session status --session <session-id>
```

**参数**：
- `--session` (必需) — session_id

**返回值**（JSON）：
```json
{
  "session_id": "sess_20260407_001",
  "session_name": "exp-web-01",
  "status": "active",
  "machine": "vm-seed-01",
  "mount_id": "mnt_20260407_001",
  "local_mount_point": "/Users/ely/workspace/research/agent/SEEDRunner/artifacts",
  "remote_work_dir": "/home/user/seed-experiment",
  "created_at": "2026-04-07T10:30:00Z",
  "last_command": "cd code && make",
  "last_exit_code": 0,
  "last_executed_at": "2026-04-07T10:30:18Z",
  "command_count": 5,
  "elapsed_seconds": 45,
  "timeout_seconds": 3600
}
```

**状态值**：
- `active` — session 正常运行
- `timeout` — session 已超时
- `error` — session 出现错误
- `destroyed` — session 已销毁

**错误处理**：
- Session 不存在 → 返回错误

---

#### 2.4 销毁 Session

**命令**：
```bash
seed-runner session destroy --session <session-id>
```

**参数**：
- `--session` (必需) — session_id

**返回值**（JSON）：
```json
{
  "session_id": "sess_20260407_001",
  "session_name": "exp-web-01",
  "status": "destroyed",
  "destroyed_at": "2026-04-07T10:31:00Z",
  "logs_preserved": true,
  "logs_location": "/Users/ely/workspace/research/agent/SEEDRunner/artifacts/logs/exp-web-01"
}
```

**行为**：
1. 销毁远程 tmux session
2. 保留本地日志和产物（便于事后审计）
3. 不卸载 mount（mount 由 Agent 显式销毁）

**错误处理**：
- Session 不存在 → 返回错误

---

## 输出目录结构

### 本地目录结构

```
<local-mount-point>/
├─ logs/                          # 所有 session 的日志
│  ├─ exp-web-01/                # Session 1 的日志
│  │  ├─ cmd_001.log
│  │  ├─ cmd_002.log
│  │  └─ ...
│  ├─ exp-crypto-02/             # Session 2 的日志
│  │  ├─ cmd_001.log
│  │  └─ ...
│  └─ ...
├─ artifacts/                     # 实验产物（来自远程 VM）
│  ├─ code/
│  ├─ results/
│  └─ ...
└─ metadata.json                  # 挂载元数据
```

### 远程目录结构

```
<remote-work-dir>/
├─ logs/                          # 所有 session 的日志（与本地同步）
│  ├─ exp-web-01/
│  │  ├─ cmd_001.log
│  │  ├─ cmd_002.log
│  │  └─ ...
│  └─ ...
├─ artifacts/                     # 实验产物
│  ├─ code/
│  ├─ results/
│  └─ ...
└─ metadata.json                  # 挂载元数据
```

### metadata.json 格式

```json
{
  "mount_id": "mnt_20260407_001",
  "machine": "vm-seed-01",
  "local_path": "/Users/ely/workspace/research/agent/SEEDRunner/artifacts",
  "remote_path": "/home/user/seed-experiment",
  "mounted_at": "2026-04-07T10:30:00Z",
  "sessions": [
    {
      "session_id": "sess_20260407_001",
      "session_name": "exp-web-01",
      "created_at": "2026-04-07T10:30:00Z",
      "commands": [
        {
          "index": 1,
          "cmd": "cd code && make",
          "log_file": "cmd_001.log",
          "exit_code": 0,
          "executed_at": "2026-04-07T10:30:15Z"
        },
        ...
      ]
    },
    ...
  ]
}
```

---

## 使用示例

### 示例 1：基本工作流

```bash
# 1. 创建挂载
$ seed-runner mount create \
    --machine vm-seed-01 \
    --local-dir /Users/ely/workspace/research/agent/SEEDRunner/artifacts

# 返回：mount_id = "mnt_20260407_001"

# 2. 创建 session（自动进入远程工作目录）
$ seed-runner session create \
    --machine vm-seed-01 \
    --mount-id mnt_20260407_001 \
    --name exp-web-01

# 返回：session_id = "sess_20260407_001"

# 3. 执行命令 1（使用相对路径）
$ seed-runner session exec \
    --session sess_20260407_001 \
    --cmd "ls -la"

# 返回：
# log_file_local = "/Users/ely/workspace/research/agent/SEEDRunner/artifacts/logs/exp-web-01/cmd_001.log"
# exit_code = 0

# 4. 执行命令 2
$ seed-runner session exec \
    --session sess_20260407_001 \
    --cmd "cd code && make"

# 返回：
# log_file_local = "/Users/ely/workspace/research/agent/SEEDRunner/artifacts/logs/exp-web-01/cmd_002.log"
# exit_code = 0

# 5. 查询状态
$ seed-runner session status --session sess_20260407_001

# 返回：status = "active", command_count = 2

# 6. 销毁 session
$ seed-runner session destroy --session sess_20260407_001

# 返回：status = "destroyed"

# 7. 销毁挂载
$ seed-runner mount destroy --mount-id mnt_20260407_001

# 返回：status = "unmounted"
```

### 示例 2：多个 session 共享一个挂载

```bash
# 1. 创建挂载（一次）
$ mount_id=$(seed-runner mount create \
    --machine vm-seed-01 \
    --local-dir ./artifacts | jq -r '.mount_id')

# 2. 创建 session 1
$ sess1=$(seed-runner session create \
    --machine vm-seed-01 \
    --mount-id $mount_id \
    --name exp-web-01 | jq -r '.session_id')

# 3. 创建 session 2（共享同一个挂载）
$ sess2=$(seed-runner session create \
    --machine vm-seed-01 \
    --mount-id $mount_id \
    --name exp-crypto-02 | jq -r '.session_id')

# 4. 在 session 1 中执行命令
$ seed-runner session exec --session $sess1 --cmd "make"

# 日志写到：./artifacts/logs/exp-web-01/cmd_001.log

# 5. 在 session 2 中执行命令
$ seed-runner session exec --session $sess2 --cmd "make"

# 日志写到：./artifacts/logs/exp-crypto-02/cmd_001.log

# 6. 销毁 session（挂载保留）
$ seed-runner session destroy --session $sess1
$ seed-runner session destroy --session $sess2

# 7. 销毁挂载
$ seed-runner mount destroy --mount-id $mount_id
```

### 示例 3：处理命令失败

```bash
# 执行一条会失败的命令
$ seed-runner session exec \
    --session sess_20260407_001 \
    --cmd "cd /nonexistent && ls"

# 返回：exit_code = 1（失败，但 session 保持 active）

# Agent 可以读取日志，理解失败原因
$ cat /Users/ely/workspace/research/agent/SEEDRunner/artifacts/logs/exp-web-01/cmd_003.log
# 输出：bash: cd: /nonexistent: No such file or directory

# Agent 可以继续执行其他命令
$ seed-runner session exec \
    --session sess_20260407_001 \
    --cmd "pwd"

# 返回：exit_code = 0（继续执行）
```

---

## 错误代码

| 错误代码 | 含义 | 处理建议 |
|---------|------|---------|
| 2001 | SSH 连接失败 | 检查网络、SSH 配置、目标机器是否在线 |
| 2002 | sshfs 挂载失败 | 检查 sshfs 是否安装、权限是否正确 |
| 2003 | tmux 创建失败 | 检查远程 VM 中 tmux 是否安装 |
| 2004 | Mount 不存在 | 检查 mount_id 是否正确 |
| 2005 | Session 不存在 | 检查 session_id 是否正确 |
| 2006 | Session 已销毁 | 创建新的 session |
| 2007 | 命令执行超时 | 增加 --timeout 参数或优化命令 |
| 2008 | 日志写入失败 | 检查本地磁盘空间、权限 |
| 2009 | 挂载卸载失败 | 检查是否有进程占用挂载点 |

---

## 设计决策

### 为什么分离挂载和 session 管理？

- **灵活性** — 一个挂载可以被多个 session 共享，减少重复挂载的开销
- **职责分离** — 挂载管理和 session 管理是两个独立的关注点
- **简化错误处理** — 挂载失败不会导致 session 创建失败

### 为什么 session 创建时自动进入工作目录？

- **透明性** — Agent 使用相对路径操作，对远程文件系统无感知
- **简化命令** — 不需要每条命令都 `cd` 到工作目录
- **一致性** — 所有 session 的初始状态相同

### 为什么日志文件名由 seed-runner 管理？

- **一致性** — 所有日志都按递增序列命名，便于排序
- **可追踪性** — 日志文件名直接反映执行顺序
- **简化 Agent** — Agent 不需要关心日志文件名

### 为什么按 session name 分组日志？

- **可读性** — 日志目录名直接反映 session 的用途
- **可维护性** — 便于查找和审计特定 session 的日志
- **隔离性** — 不同 session 的日志互不干扰

### 为什么整体执行命令？

- **符合 shell 语义** — `cd /path && make && ./test` 作为一个整体执行
- **减少黑盒** — Agent 能清楚地看到每条命令的完整执行过程
- **便于调试** — 失败时能准���定位问题

### 为什么不销毁失败的 session？

- **模拟真实终端** — 真实的终端中，命令失败不会关闭终端
- **支持调试** — Agent 可以继续执行其他命令来诊断问题
- **提高容错性** — 允许 Agent 自动重试或调整策略

---

## 实现注意事项

### 字符转义

`seed-runner session exec` 必须正确处理以下特殊字符：
- Shell 元字符：`$`, `"`, `'`, `\`, `|`, `&`, `;`, `>`, `<`, `(`, `)`, `{`, `}`, `[`, `]`
- 空格和制表符
- 换行符（多行命令）

**实现方式**：使用 `shlex.quote()` (Python) 或等价的转义函数。

### 日志文件命名

- 使用递增的数字前缀：`cmd_001.log`, `cmd_002.log`, ...
- 便于按执行顺序排序
- 时间戳在日志内容中记录

### 超时处理

- 单条命令超时：返回超时错误，但 session 保持 active
- 整个 session 超时：标记 session 状态为 `timeout`，后续命令返回错误

### 网络中断恢复

- 如果 SSH 连接中断，`seed-runner session exec` 应尝试重连（最多 3 次）
- 如果 sshfs 挂载断开，自动重新挂载
- 如果无法恢复，返回错误，session 标记为 `error`

### 初始工作目录设置

- Session 创建时，在 tmux session 中执行 `cd <remote_work_dir>`
- 后续所有命令都在这个目录下执行
- Agent ��以使用相对路径操作

---

## 后续扩展

### 可能的增强功能

1. **批量执行** — 支持一次性执行多条命令
2. **条件执行** — 支持 `if exit_code == 0 then ...`
3. **并行执行** — 支持在多个 pane 中并行执行
4. **交互式命令** — 支持需要用户输入的命令（如 `sudo`）
5. **文件传输** — 支持本地 ↔ 远程的文件传输
6. **后台任务** — 支持 `--background` 参数，后台执行并返回 job_id

这些功能可以在后续版本中逐步添加，不影响当前的最小化设计。
