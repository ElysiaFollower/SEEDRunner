
last updated: 2026-04-08  08:34

第一次在真实任务下全量测试发现如下几个问题：

1. 遇到一个平台级前置问题：seed-runner 只会从当前工作目录找 .env.machines，而实验目录下没有这份配置，所以第一次 mount create 被直接拒绝。
- 推荐解决方案： 不是从脚本的运行路径开始检索， 而是以脚本文件所在的位置开始相对检索， 这样能保证找到.env.machines


2. 这里暴露出另一个平台问题：session create 返回了成功 JSON，但后续 session exec 立刻报 Session not found; 问题原因找到了，不是远端 tmux 挂了，而是我刚才把 session create 和 mount status 并行跑了；这两个命令都读写同一个 ~/.seed-runner/state.json，后写入的 mount status 把刚创建的 session 状态覆盖掉了。这个属于本地状态竞争，不影响 mount，本轮我
  会串行重建 session 并继续。

3. 我又踩到一个状态竞争点：同一个 session 上并发执行两条 session exec，seed-runner 没有做命令编号互斥，结果两次都写成了 cmd_001.log。后续我不会再对同一 session 并发发命令，先把当前日志读出来确认环境，然后串行推进。

4. 容器拉起失败的原因不是 Docker 本身，而是 attacker 服务把 ./volumes 绑定到一个经 sshfs 挂载的路径上，Docker 对这种 FUSE 路径处理不稳定。解决办法是把 Labsetup 和代码先复制到远端本地磁盘目录，再从那个本地目录启动容器；实验结果仍然写回共享目
  录。
推荐解决方案：内置一个copy命令用于把挂载目录同步复制到宿主机的特定目录， 返回的路径为copy过去后再远程机上真实存在的路径， 而实际的挂载点可能在附近的一个mounted文件夹。从而将同步的负担向Agent隐藏。

5. 宿主共享目录里的 ELF 二进制也被 sudo 拒绝执行了，和前面的 root 读脚本问题一样，都是 sshfs/noexec 边界. 这个和问题4本质相同。 都是源自于挂载目录的特殊性， 解决方法也类似。 不过这个发现暴露出另一个问题——就是最小初始集除了两台机器彼此持有对方的ssh公钥以外， 还需要允许远端用户seed能够无密码执行sudo