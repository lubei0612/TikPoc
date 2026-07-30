# TikPoc 单一 Wheel 部署手册

## 目标

服务器只运行一个由明确 Git commit 构建的 wheel。不要分别复制
`src/tikpoc` 和虚拟环境 `site-packages/tikpoc`，也不要从工作目录源码隐式导入。

## 构建

```bash
git status --short
COMMIT="$(git rev-parse --short HEAD)"
uv run pytest -q
uv build --wheel --out-dir dist
```

构建产物、commit 和锁文件共同组成一次发布。wheel 不提交到 Git。

## 发布目录

```text
/opt/tikpoc/
  releases/
    COMMIT/
      app.whl
      venv/
  current -> releases/COMMIT
/etc/tikpoc/tikpoc.env
/var/lib/tikpoc/tikpoc.db
```

环境文件权限设为 `0600`，只保存运行密钥和公开地址，不写入仓库。

## 安装新版本

```bash
python3.12 -m venv "/opt/tikpoc/releases/$COMMIT/venv"
"/opt/tikpoc/releases/$COMMIT/venv/bin/pip" install \
  "/opt/tikpoc/releases/$COMMIT/app.whl"
TIKPOC_BUILD_COMMIT="$COMMIT" \
  "/opt/tikpoc/releases/$COMMIT/venv/bin/python" -c \
  'from tikpoc.runtime_metadata import runtime_metadata; print(runtime_metadata())'
```

验证输出中的 `build_commit`、`policy_version`、`device_protocol_version` 后，原子
切换 `current` 链接并重启服务。systemd 模板位于
`deploy/systemd/tikpoc.service.example`。

systemd 的 `ExecStart` 必须引用 `/opt/tikpoc/current/.../tikpoc`，不得固化某个
`releases/COMMIT` 路径。切换后立即核对实际启动命令：

```bash
systemctl show -p ExecStart --value tikpoc
readlink -f /opt/tikpoc/current
```

两者必须指向同一发布版本；仅切换软链接但服务仍启动旧 release 不算部署成功。

## 部署后门槛

```bash
curl --fail http://127.0.0.1:8765/api/runtime
systemctl is-active tikpoc
journalctl -u tikpoc --since '-2 minutes' --no-pager
```

确认：

1. `/api/runtime` 的 commit 与发布 commit 相同；
2. 数据库迁移成功；
3. 服务只从 `/opt/tikpoc/current/venv` 导入 `tikpoc`；
4. 暂停轮次仍为暂停，不因部署自动启动；
5. 移动心跳恢复后再安排独立 canary。

## 回滚

把 `current` 原子切回上一 release，重启并复查 `/api/runtime`。数据库结构若发生
不可逆变化，必须使用该版本对应的迁移回滚或备份；不要用旧 wheel 强行读取未知
schema。
