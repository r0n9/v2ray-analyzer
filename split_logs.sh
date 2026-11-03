#!/bin/bash

# 1. 将 access.log 复制为 original.log
cp access.log original.log

# 2. 将 access.log 置空
> access.log

# 3. 删除 rejected 的记录，以及 TCP 端口号为 53 的 DNS 记录，得到 accepted.log
grep -v 'rejected' original.log | grep -v 'tcp:.*:53' > accepted.log

# 4. 删除 UDP 地址全部为 IP 的记录，得到 accepted_2.log
grep -v 'udp:[0-9]*\.[0-9]*\.[0-9]*\.[0-9]*' accepted.log > accepted_2.log

# 5. 处理 accepted_2.log，去除 accepted 列和 [direct] 列，得到 target.log
awk '{print $1, $2, $3, $4, $5}' accepted_2.log > target.log

# 6. 拆分 target.log 文件，根据日期（第一列）拆分为不同的文件
awk '{
    # 将日期中的 / 替换为 _
    file_name = $1;
    gsub(/\//, "_", file_name);  # 将 / 替换为 _
    file_name = file_name ".log";  # 生成最终的文件名
    
    print $0 >> file_name  # 输出到相应的文件中
}' target.log

# 7. 删除临时文件
rm -f original.log accepted.log accepted_2.log target.log

