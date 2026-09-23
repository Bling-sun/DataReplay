# DataReplay

基于 Python 和原生 HTML/JavaScript 的 LeRobot 数据集回放平台。支持多来源数据集浏览、Episode 切换、多视角视频同步回放，以及 observation/action 关节曲线查看。

## 类别时长统计

首页展示茶艺、双脑、快递分拣的总时长、数据集数和 Episode 数。统计范围是全部已加载数据来源，不随当前任务或来源筛选变化，点击“刷新目录”同步更新。时长按各数据集帧数除以各自 FPS 后累加，不叠加多个摄像头的时长；不同来源中的数据集分别计入（包括清洗训练集）。没有数据的类别显示零。

## 环境与启动

需要 Python 3.10+，后台运行需要 `tmux`。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run_server.sh --source '任务=数据来源=/path/to/dataset/root'
```

可重复传入 `--source`。数据集需包含 `meta/info.json`、`meta/episodes.jsonl`，以及对应的 Parquet 和视频文件。访问 `http://127.0.0.1:7865`。

不传参数时，脚本使用 A800 已有的四个数据来源；Python 优先使用本项目 `.venv`，其次使用 A800 已有的 RLinf 虚拟环境，最后使用 `python3`。可通过 `DATAREPLAY_PYTHON`、`DATAREPLAY_HOST` 和 `DATAREPLAY_PORT` 覆盖配置。

## A800 后台管理

项目位置：`/mnt/sunbing/projects/DataReplay`。

```bash
cd /mnt/sunbing/projects/DataReplay
./service.sh start
./service.sh status
./service.sh logs
./service.sh restart
./service.sh stop
```

后台服务运行在 `datareplay` tmux 会话中，SSH 断开后继续运行；服务异常退出后等待 5 秒自动重启。日志为 `runtime/server.log`。也可使用 `./service.sh start --source '任务=来源=/path'` 传入自定义参数；重启时需要再次传入这些参数。

A800 当前容器没有运行 systemd，此方式不提供容器/主机重启后的自动启动；重启后执行 `./service.sh start`。

## SSH 访问

在自己的电脑执行：

```bash
ssh -N -L 7865:127.0.0.1:7865 A800
```

然后访问 `http://127.0.0.1:7865`。如需同一局域网共享，另开转发并将 `<本机局域网IP>` 替换成实际地址：

```bash
ssh -N -L <本机局域网IP>:7865:127.0.0.1:7865 A800
```

同事访问 `http://<本机局域网IP>:7865`；转发电脑需要保持在线。平台不含登录认证，适用于可信内部网络。

## 仓库

- GitLab：`git@gitlab.roboscience.xyz:CNS2026101003/datareplay.git`
- GitHub：`git@github.com:Bling-sun/DataReplay.git`

只提交代码和文档。数据集、视频、日志、运行时 PID、虚拟环境和历史备份不纳入版本控制。
