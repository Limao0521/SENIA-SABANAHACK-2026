# Simulador oficial AgiBot X2 (AimDK v1.1.0, mc/sim_mujoco x86 v1.0.0).
#
# Mismo entorno que la imagen x2-simulation de AgiBot, pero sobre una base local ROS 2 Humble
# (por defecto g1-ros2-humble:local; vale cualquier ros:humble-ros-base) y solo con las librerías que
# enlazan los binarios entregados. Los binarios y el SDK se quedan en el host y se montan en /workspace.
# python3-tk: ventana de teclado (official_sim/keyboard_teleop.py).
ARG BASE_IMAGE=g1-ros2-humble:local
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp

# Check-Date=false: si el reloj del host va atrasado, apt rechaza índices "aún no válidos".
RUN apt-get -o Acquire::Check-Date=false update && apt-get install -y --no-install-recommends \
      ros-humble-rmw-fastrtps-cpp \
      ros-humble-rosidl-default-generators \
      ros-humble-grid-map-ros \
      ros-humble-grid-map-msgs \
      ros-humble-tf2-ros \
      ros-humble-octomap-msgs \
      ros-humble-pcl-msgs \
      ros-humble-map-msgs \
      ros-humble-plotjuggler-msgs \
      ros-humble-test-msgs \
      libboost-filesystem1.74.0 \
      libassimp5 \
      libccd2 \
      liboctomap1.9 \
      libpcl-common1.12 \
      libnlopt0 \
      liborocos-kdl1.5 \
      libfmt8 \
      libgflags2.2 \
      libscrypt0 \
      libyaml-cpp0.7 \
      libcurl4 \
      libncurses6 \
      libgl1 \
      libgl1-mesa-dri \
      libglx-mesa0 \
      libegl1 \
      libglfw3 \
      x11-utils \
      python3-numpy \
      python3-tk \
      tmux \
    && rm -rf /var/lib/apt/lists/*

RUN printf '%s\n' \
      'source /opt/ros/humble/setup.bash' \
      'export RMW_IMPLEMENTATION=rmw_fastrtps_cpp' \
      'if [ -f /workspace/aimdk/install/local_setup.bash ]; then source /workspace/aimdk/install/local_setup.bash; fi' \
      >> /root/.bashrc

WORKDIR /workspace
ENTRYPOINT []
CMD ["sleep", "infinity"]
