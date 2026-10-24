# aimdk_msgs = todas las definiciones del AimDK v1.1.0 + McBodyPose del robot, compilado desde fuente.
cmake_minimum_required(VERSION 3.8)
project(aimdk_msgs)
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
foreach(dep std_msgs action_msgs geometry_msgs sensor_msgs nav_msgs builtin_interfaces)
  find_package(${dep} REQUIRED)
endforeach()
file(GLOB MSG_FILES RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} "${CMAKE_CURRENT_SOURCE_DIR}/msg/*.msg")
file(GLOB SRV_FILES RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} "${CMAKE_CURRENT_SOURCE_DIR}/srv/*.srv")
rosidl_generate_interfaces(${PROJECT_NAME} ${MSG_FILES} ${SRV_FILES}
  DEPENDENCIES std_msgs action_msgs geometry_msgs sensor_msgs nav_msgs builtin_interfaces)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
