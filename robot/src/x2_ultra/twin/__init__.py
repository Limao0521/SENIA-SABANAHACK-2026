"""Gemelo digital del X2 Ultra-N: nuestro MuJoCo (Ultra-N + OmniHand) hace de HAL del robot real.

El MC oficial de AgiBot se conecta a él igual que al robot (mismos topics, mensajes, QoS y frecuencias), y
encima van las cámaras, el LiDAR y el audio con los nombres de AimDK. Los módulos ``contract``, ``model`` y
``physics`` no dependen de ROS (se prueban con pytest en el venv); los ``ros_*`` corren en el contenedor ROS 2
Humble (``official_sim/twin.sh``).
"""
