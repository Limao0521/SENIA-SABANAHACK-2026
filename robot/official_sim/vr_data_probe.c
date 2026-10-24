/* Experimental VRData publisher for the isolated X2 digital twin only.
 *
 * The public AimDK omits VRData, but MC x86 v1.0.0 ships its C ROSIDL type
 * support. The two structs below reproduce the ABI verified against that
 * library's ROSIDL introspection. This tool is deliberately bounded: DDS 77,
 * networkless container, <= 0.4 joystick input, <= 5 s command duration.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <ifaddrs.h>
#include <math.h>
#include <net/if.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "geometry_msgs/msg/detail/quaternion__struct.h"
#include "geometry_msgs/msg/detail/vector3__struct.h"
#include "rcl/rcl.h"
#include "rcutils/error_handling.h"
#include "rosidl_runtime_c/string_functions.h"
#include "std_msgs/msg/detail/header__struct.h"

typedef struct {
  rosidl_runtime_c__String name;
  uint32_t id;
  bool key_one;
  bool key_two;
  double hand_trig;
  double index_trig;
  double axis_x;
  double axis_y;
  bool axis_click;
  geometry_msgs__msg__Vector3 position;
  geometry_msgs__msg__Quaternion orientation;
} VRControllerState;

typedef struct {
  VRControllerState *data;
  size_t size;
  size_t capacity;
} VRControllerStateSequence;

typedef struct {
  std_msgs__msg__Header header;
  uint32_t status;
  uint32_t err_code;
  VRControllerStateSequence vr_controller_states;
} VRData;

_Static_assert(sizeof(VRControllerState) == 128, "MC VRControllerState ABI changed");
_Static_assert(offsetof(VRControllerState, axis_x) == 48, "MC VRControllerState ABI changed");
_Static_assert(offsetof(VRControllerState, axis_y) == 56, "MC VRControllerState ABI changed");
_Static_assert(offsetof(VRControllerState, orientation) == 96, "MC VRControllerState ABI changed");
_Static_assert(sizeof(VRData) == 64, "MC VRData ABI changed");
_Static_assert(offsetof(VRData, vr_controller_states) == 40, "MC VRData ABI changed");

extern VRData *aimdk_msgs__msg__VRData__create(void);
extern void aimdk_msgs__msg__VRData__destroy(VRData *);
extern bool aimdk_msgs__msg__VRControllerState__Sequence__init(VRControllerStateSequence *, size_t);
extern const rosidl_message_type_support_t *
rosidl_typesupport_c__get_message_type_support_handle__aimdk_msgs__msg__VRData(void);

static volatile sig_atomic_t interrupted = 0;
static void on_signal(int sig) { (void)sig; interrupted = 1; }

static double monotonic_seconds(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec * 1e-9;
}

static void sleep_20ms(void) {
  struct timespec t = {.tv_sec = 0, .tv_nsec = 20000000L};
  while (nanosleep(&t, &t) && errno == EINTR && !interrupted) {}
}

static int require_isolated_sim(void) {
  const char *sim = getenv("X2_SIM_ONLY"), *local = getenv("ROS_LOCALHOST_ONLY"),
             *domain = getenv("ROS_DOMAIN_ID");
  if (!sim || strcmp(sim, "1") || !local || strcmp(local, "1") ||
      !domain || strcmp(domain, "77")) {
    fprintf(stderr, "VR probe requires X2_SIM_ONLY=1, ROS_LOCALHOST_ONLY=1 and ROS_DOMAIN_ID=77\n");
    return 0;
  }
  struct ifaddrs *all = NULL;
  if (getifaddrs(&all) != 0) return 0;
  int isolated = 1;
  for (const struct ifaddrs *p = all; p; p = p->ifa_next) {
    if (p->ifa_name && (p->ifa_flags & IFF_UP) && !(p->ifa_flags & IFF_LOOPBACK)) {
      fprintf(stderr, "VR probe refused: non-loopback interface %s is active\n", p->ifa_name);
      isolated = 0;
      break;
    }
  }
  freeifaddrs(all);
  return isolated;
}

static void usage(const char *name) {
  fprintf(stderr, "Usage: %s [--forward -0.4..0.4] [--lateral -0.4..0.4] "
                  "[--yaw -0.4..0.4] [--seconds 0.1..5] [--lead 0..5]\n", name);
}

static int parse_number(const char *s, double *out) {
  char *end;
  errno = 0;
  *out = strtod(s, &end);
  return errno == 0 && end != s && *end == '\0' && isfinite(*out);
}

int main(int argc, char **argv) {
  double forward = 0.0, lateral = 0.0, yaw = 0.0, seconds = 2.0, lead = 1.0;
  if (!require_isolated_sim()) return 2;
  for (int i = 1; i < argc; i += 2) {
    if (i + 1 >= argc) { usage(argv[0]); return 2; }
    double value;
    if (!parse_number(argv[i + 1], &value)) { usage(argv[0]); return 2; }
    if (!strcmp(argv[i], "--forward")) forward = value;
    else if (!strcmp(argv[i], "--lateral")) lateral = value;
    else if (!strcmp(argv[i], "--yaw")) yaw = value;
    else if (!strcmp(argv[i], "--seconds")) seconds = value;
    else if (!strcmp(argv[i], "--lead")) lead = value;
    else { usage(argv[0]); return 2; }
  }
  if (fabs(forward) > 0.4 || fabs(lateral) > 0.4 || fabs(yaw) > 0.4 ||
      seconds < 0.1 || seconds > 5.0 || lead < 0.0 || lead > 5.0) {
    usage(argv[0]);
    return 2;
  }
  signal(SIGINT, on_signal);
  signal(SIGTERM, on_signal);

  VRData *msg = aimdk_msgs__msg__VRData__create();
  if (!msg || !aimdk_msgs__msg__VRControllerState__Sequence__init(&msg->vr_controller_states, 3)) {
    fprintf(stderr, "Cannot allocate VRData\n");
    return 1;
  }
  const char *names[] = {"left", "right", "head"};
  for (size_t i = 0; i < 3; ++i) {
    VRControllerState *s = &msg->vr_controller_states.data[i];
    if (!rosidl_runtime_c__String__assign(&s->name, names[i])) return 1;
    s->id = (uint32_t)i;
    s->orientation.w = 1.0;
  }
  /* Converter accepts only status==0; head is third by sequence position. */
  msg->status = 0;
  msg->err_code = 0;
  rosidl_runtime_c__String__assign(&msg->header.frame_id, "x2_twin_vr_probe");

  rcl_allocator_t allocator = rcl_get_default_allocator();
  rcl_init_options_t init_options = rcl_get_zero_initialized_init_options();
  rcl_context_t context = rcl_get_zero_initialized_context();
  rcl_node_t node = rcl_get_zero_initialized_node();
  rcl_publisher_t pub = rcl_get_zero_initialized_publisher();
  rcl_node_options_t node_options = rcl_node_get_default_options();
  if (rcl_init_options_init(&init_options, allocator) != RCL_RET_OK ||
      rcl_init(0, NULL, &init_options, &context) != RCL_RET_OK ||
      rcl_node_init(&node, "x2_vr_probe", "", &context, &node_options) != RCL_RET_OK) {
    fprintf(stderr, "Could not initialize ROS node: %s\n", rcutils_get_error_string().str);
    return 1;
  }
  const rosidl_message_type_support_t *ts =
      rosidl_typesupport_c__get_message_type_support_handle__aimdk_msgs__msg__VRData();
  rcl_publisher_options_t pub_options = rcl_publisher_get_default_options();
  if (rcl_publisher_init(&pub, &node, ts, "/aima/teleop_bridge/vr_data", &pub_options) != RCL_RET_OK) {
    fprintf(stderr, "Could not create VRData publisher: %s\n", rcutils_get_error_string().str);
    return 1;
  }
  size_t subscribers = 0;
  unsigned sent = 0;
  int exit_code = 0;
  double wait_until = monotonic_seconds() + 5.0;
  while (!interrupted && monotonic_seconds() < wait_until) {
    if (rcl_publisher_get_subscription_count(&pub, &subscribers) == RCL_RET_OK && subscribers > 0) break;
    sleep_20ms();
  }
  if (subscribers == 0) {
    fprintf(stderr, "No MC subscriber on /aima/teleop_bridge/vr_data\n");
    exit_code = 1;
    goto cleanup;
  }
  const double start = monotonic_seconds();
  const double end = start + lead + seconds;
  const double stop = end + 0.5;
  while (!interrupted && monotonic_seconds() < stop) {
    double now = monotonic_seconds();
    bool move = now >= start + lead && now < end;
    msg->vr_controller_states.data[0].axis_x = move ? lateral : 0.0;
    msg->vr_controller_states.data[0].axis_y = move ? forward : 0.0;
    msg->vr_controller_states.data[1].axis_x = move ? yaw : 0.0;
    struct timespec wall;
    clock_gettime(CLOCK_REALTIME, &wall);
    msg->header.stamp.sec = (int32_t)wall.tv_sec;
    msg->header.stamp.nanosec = (uint32_t)wall.tv_nsec;
    if (rcl_publish(&pub, msg, NULL) != RCL_RET_OK) {
      fprintf(stderr, "VRData publish failed: %s\n", rcutils_get_error_string().str);
      exit_code = 1;
      break;
    }
    ++sent;
    sleep_20ms();
  }
cleanup:
  /* A stop message is always sent before leaving, including SIGINT/SIGTERM. */
  msg->vr_controller_states.data[0].axis_x = 0.0;
  msg->vr_controller_states.data[0].axis_y = 0.0;
  msg->vr_controller_states.data[1].axis_x = 0.0;
  for (int i = 0; subscribers > 0 && i < 25; ++i) {
    if (rcl_publish(&pub, msg, NULL) != RCL_RET_OK) break;
    sleep_20ms();
  }
  fprintf(stdout, "VRData: %u messages, subscribers=%zu, left=(%.2f,%.2f), right_x=%.2f\n",
          sent, subscribers, lateral, forward, yaw);
  int cleanup_errors = 0;
  cleanup_errors += rcl_publisher_fini(&pub, &node) != RCL_RET_OK;
  cleanup_errors += rcl_node_fini(&node) != RCL_RET_OK;
  cleanup_errors += rcl_shutdown(&context) != RCL_RET_OK;
  cleanup_errors += rcl_context_fini(&context) != RCL_RET_OK;
  cleanup_errors += rcl_init_options_fini(&init_options) != RCL_RET_OK;
  aimdk_msgs__msg__VRData__destroy(msg);
  return interrupted ? 130 : (cleanup_errors || exit_code ? 1 : 0);
}
