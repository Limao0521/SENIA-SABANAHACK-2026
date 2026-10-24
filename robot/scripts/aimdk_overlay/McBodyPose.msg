# Copiado del X2 real (/agibot/software/common/share/aimdk_msgs/msg/McBodyPose.msg, lx2501 v1.1.4,
# 20/10/2026): el AimDK público v1.1.0 lo retiró. Topic /aima/mc/body_pose; lo publican el mando y la app.
# 消息头
MessageHeader header

# 输入源
string source

# 站立高度 (站直/下蹲)， 正值站立， 负值下蹲
# 单位：m，距离地面的高度
float64 stand_height

# 身体前倾/后倾, 正值前倾， 负值后倾
# 单位：rad
float64 bend_forward

# 身体侧弯, 正值左侧弯， 负值右侧弯
# 单位：rad
float64 bend_sideways

# 腰部旋转， 正值左转， 负值右转
# 单位：rad
float64 twist_waist

# 点头/抬头，正值低头，负值抬头
# 单位：rad
float64 raise_head

# 头部旋转，正值左转，负值右转
# 单位：rad
float64 turn_head
