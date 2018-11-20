#!/usr/bin/env python2.7

from functools import partial

import numpy as np

import rospy
import tf
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool, Float64
from youbot_position.srv import PositionControl, PositionControlResponse


class Controller(object):
  '''A position control service node'''

  def __init__(self,
               global_frame='world',
               youbot_frame='vicon/Mufasa_Base/Mufasa_Base',
               setpoint_topic_format='setpoint_{}',
               control_topic_format='control_{}'):
    # Initialize constants
    self.stopping_distance = rospy.get_param('stopping_distance', 0.15)
    self.velocity_scale = 0.1

    # Initialize system state
    self.fresh_val = {'x': False, 'y': False}
    self.velocity = Twist()
    self.frames = {'target': global_frame, 'source': youbot_frame}
    self.goal = np.zeros(2)
    self.stopped = True

    # Setup publishers for setpoints, velocity, and PID enabling
    self.setpoint_pub = {
        'x': rospy.Publisher(setpoint_topic_format.format('x'), Float64, queue_size=5, latch=True),
        'y': rospy.Publisher(setpoint_topic_format.format('y'), Float64, queue_size=5, latch=True)
    }

    self.velocity_pub = rospy.Publisher('mufasa/cmd_vel', Twist, queue_size=10)
    self.pid_enable_pub = rospy.Publisher('pid_enable', Bool, queue_size=10)

    # Setup transform listener for pose transform
    self.tf_listener = tf.TransformListener()

    rospy.logwarn('Waiting for transform from {} to {}...'.format(global_frame, youbot_frame))
    self.tf_listener.waitForTransform(self.frames['target'], self.frames['source'], rospy.Time(),
                                      rospy.Duration(15))

    # Setup subscribers for control effort
    self.control_sub = {
        'x':
            rospy.Subscriber(
                control_topic_format.format('x'), Float64, partial(self.control_callback, 'x')),
        'y':
            rospy.Subscriber(
                control_topic_format.format('y'), Float64, partial(self.control_callback, 'y'))
    }

    # Start with PID disabled
    self.disable_control()

    # Setup timer to disable control when the setpoint is reached
    self.prox_timer = rospy.Timer(rospy.Duration(0.2), self.pose_callback)

    # Initialize service that will accept requested positions and set PID setpoints to match
    self.control_service = rospy.Service('position_control', PositionControl,
                                         self.position_control_service)
    rospy.logwarn("Position control service running!")

  # Executes when service is called; sets set_velocity to True
  def position_control_service(self, req):
    '''Callback receiving control service requests'''
    if req.stop:
      self.disable_control()
      self.velocity_pub.publish(Twist())
      return PositionControlResponse()

    rospy.logwarn("Received request: %s", req)
    self.goal = np.array([req.x, req.y])
    self.setpoint_pub['x'].publish(req.x)
    self.setpoint_pub['y'].publish(req.y)
    self.stopped = False
    self.pid_enable_pub.publish(True)
    return PositionControlResponse()

  def disable_control(self):
    '''Disable the PID controllers'''
    self.stopped = True
    self.pid_enable_pub.publish(False)

  def control_callback(self, dimension, control):
    '''Callback receiving PID control output'''
    if self.stopped:
      return

    if dimension == 'x':
      self.velocity.linear.x = control.data
    elif dimension == 'y':
      self.velocity.linear.y = control.data

    self.fresh_val[dimension] = True
    if self.fresh_val['x'] and self.fresh_val['y']:
      self.velocity_pub.publish(self.velocity)
      self.fresh_val['x'] = False
      self.fresh_val['y'] = False

  def pose_callback(self, _):
    '''Handle stopping if the current pose is close enough to the goal'''
    (pose_x, pose_y, _), _ = self.tf_listener.lookupTransform(self.frames['target'],
                                                              self.frames['source'], rospy.Time())
    pose = np.array([pose_x, pose_y])
    diff = pose - self.goal
    dist = diff.dot(diff)
    rospy.logwarn('Got distance: %s', dist)
    if dist <= self.stopping_distance:
      self.disable_control()
      self.velocity_pub.publish(Twist())


# Spin on control_service so that if the service goes down, we stop. This is better than doing it on
# rospy itself because it's more specific (per
# http://wiki.ros.org/rospy/Overview/Services#A.28Waiting_for.29_shutdown)
if __name__ == "__main__":
  rospy.init_node('position_control')
  C = Controller()
  C.control_service.spin()
