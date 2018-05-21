import argparse
import time
import msgpack
from enum import Enum, auto

import numpy as np

from planning_utils import a_star, heuristic, create_grid
from udacidrone import Drone
from udacidrone.connection import MavlinkConnection
from udacidrone.messaging import MsgID
from udacidrone.frame_utils import global_to_local, local_to_global
import utm
import matplotlib.pyplot as plt


class States(Enum):
    MANUAL = auto()
    ARMING = auto()
    TAKEOFF = auto()
    WAYPOINT = auto()
    LANDING = auto()
    DISARMING = auto()
    PLANNING = auto()


class MotionPlanning(Drone):

    def __init__(self, connection):
        super().__init__(connection)

        self.target_position = np.array([0.0, 0.0, 0.0])
        self.waypoints = []
        self.in_mission = True
        self.check_state = {}

        # initial state
        self.flight_state = States.MANUAL

        # register all your callbacks here
        self.register_callback(MsgID.LOCAL_POSITION, self.local_position_callback)
        self.register_callback(MsgID.LOCAL_VELOCITY, self.velocity_callback)
        self.register_callback(MsgID.STATE, self.state_callback)

    def local_position_callback(self):
        if self.flight_state == States.TAKEOFF:
            if -1.0 * self.local_position[2] > 0.95 * self.target_position[2]:
                self.waypoint_transition()
        elif self.flight_state == States.WAYPOINT:
            if np.linalg.norm(self.target_position[0:2] - self.local_position[0:2]) < 1.0:
                if len(self.waypoints) > 0:
                    self.waypoint_transition()
                else:
                    if np.linalg.norm(self.local_velocity[0:2]) < 1.0:
                        self.landing_transition()

    def velocity_callback(self):
        if self.flight_state == States.LANDING:
            if self.global_position[2] - self.global_home[2] < 0.1:
                if abs(self.local_position[2]) < 0.01:
                    self.disarming_transition()

    def state_callback(self):
        if self.in_mission:
            if self.flight_state == States.MANUAL:
                self.arming_transition()
            elif self.flight_state == States.ARMING:
                if self.armed:
                    self.plan_path()
            elif self.flight_state == States.PLANNING:
                self.takeoff_transition()
            elif self.flight_state == States.DISARMING:
                if ~self.armed & ~self.guided:
                    self.manual_transition()

    def arming_transition(self):
        self.flight_state = States.ARMING
        print("arming transition")
        self.arm()
        self.take_control()

    def takeoff_transition(self):
        self.flight_state = States.TAKEOFF
        print("takeoff transition")
        self.takeoff(self.target_position[2])

    def waypoint_transition(self):
        self.flight_state = States.WAYPOINT
        print("waypoint transition")
        self.target_position = self.waypoints.pop(0)
        print('target position', self.target_position)
        self.cmd_position(self.target_position[0], self.target_position[1], self.target_position[2], self.target_position[3])

    def landing_transition(self):
        self.flight_state = States.LANDING
        print("landing transition")
        self.land()

    def disarming_transition(self):
        self.flight_state = States.DISARMING
        print("disarm transition")
        self.disarm()
        self.release_control()

    def manual_transition(self):
        self.flight_state = States.MANUAL
        print("manual transition")
        self.stop()
        self.in_mission = False

    def send_waypoints(self):
        print("Sending waypoints to simulator ...")
        data = msgpack.dumps(self.waypoints)
        self.connection._master.write(data)


    def point(self,p):
        return np.array([p[0], p[1], 1.]).reshape(1, -1)

    def collinearity_check(self,p1, p2, p3, epsilon=1e-6):   
        m = np.concatenate((p1, p2, p3), 0)
        det = np.linalg.det(m)
        return abs(det) < epsilon

        
    def prune_path(self,path):
        if path is not None:
            pruned_path = [p for p in path]
            # TODO: prune the path!
            i = 0
            while i < len(pruned_path)-2:
                p1 = self.point(pruned_path[i])
                p2 = self.point(pruned_path[i + 1])
                p3 = self.point(pruned_path[i + 2])
                if self.collinearity_check(p1,p2,p3):
                    pruned_path.remove(pruned_path[i+1])
                else:
                    i = i + 1
        else:
            pruned_path = path

        return pruned_path

    def plan_path(self):
        self.flight_state = States.PLANNING
        print("Searching for a path ...")
        TARGET_ALTITUDE = 5
        SAFETY_DISTANCE = 5

        self.target_position[2] = TARGET_ALTITUDE

        # TODO: read lat0, lon0 from colliders into floating point values
        file = open('colliders.csv', 'r')

        firstLine = file.readline()

        lat0, lon0 = firstLine.split(',')
        
        lat0 = lat0.strip('lat0 ')
        
        lon0 = lon0.strip('lon0 ')
    

        # TODO: set home position to (lon0, lat0, 0)
        self.set_home_position(lon0, lat0, 0)
        
        # TODO: retrieve current global position
        global_position = [self._latitude, self._longitude, self._altitude]
        
        # TODO: convert to current local position using global_to_local()
        local_position = global_to_local(self.global_position,self.global_home)
        
        print('global home {0}, position {1}, local position {2}'.format(self.global_home, self.global_position,self.local_position))
                                                                         
        # Read in obstacle map
        data = np.loadtxt('colliders.csv', delimiter=',', dtype='Float64', skiprows=2)
        
        # Define a grid for a particular altitude and safety margin around obstacles
        grid, north_offset, east_offset = create_grid(data, TARGET_ALTITUDE, SAFETY_DISTANCE)
        
        print("North offset = {0}, east offset = {1}".format(north_offset, east_offset))
        
        # Define starting point on the grid (this is just grid center)
        grid_start = (int(local_position[0]-north_offset), int(local_position[1]-east_offset))
        # TODO: convert start position to current position rather than map center
        
        # Set goal as some arbitrary position on the grid and check if that position is an obstacle,
        #if not, assign that position as the random goal

       # Set goal as some arbitrary position on the grid
        # example: grid_goal = (-north_offset + 10, -east_offset + 10)
        # Done: adapt to set goal as latitude / longitude position and convert
        goalPossible = False
        while not goalPossible:
            random_number = 300
            random_goal_coordinate = local_to_global([local_position[0]+np.random.uniform(-random_number, random_number),
                            local_position[1]+np.random.uniform(-random_number, random_number),
                            0.0], self.global_home)
            # goal_coordinate = [lat,lon,up]
            goal_coordinate = random_goal_coordinate
            goal_position = global_to_local(goal_coordinate, self.global_home)

            grid_goal = (int(goal_position[0])-north_offset, int(goal_position[1])-east_offset)
            goalPossible = grid[grid_goal[0]][grid_goal[1]] != 1
        
        print('global goal position {}'.format(goal_position))
        # TODO: adapt to set goal as latitude / longitude position and convert

        # Run A* to find a path from start to goal
        # TODO: add diagonal motions with a cost of sqrt(2) to your A* implementation
        # or move to a different search space such as a graph (not done here)
        print('Local Start and Goal: ', grid_start, grid_goal)
        path, _ = a_star(grid, heuristic, grid_start, grid_goal)

        #Plot the grid map
        plt.rcParams['figure.figsize'] = 12, 12
        plt.imshow(grid, cmap='Greys', origin='lower')
        plt.xlabel('East')
        plt.ylabel('North')
        green_start = plt.plot(grid_start[1], grid_start[0], 'go', markersize=10)
        red_end = plt.plot(grid_goal[1], grid_goal[0], 'ro', markersize=10)
        if path is not None:
            pp = np.array(path)
            plt.plot(pp[:, 1], pp[:, 0], 'b')
        plt.legend([green_start, red_end], ["Start", "End"])
        plt.show()


        # TODO: prune path to minimize number of waypoints
        # TODO (if you're feeling ambitious): Try a different approach altogether!
        pruned_path = self.prune_path(path)
        print("pruned path",pruned_path)

        # Convert path to waypoints
        waypoints = [[p[0] + north_offset, p[1] + east_offset, TARGET_ALTITUDE, 0] for p in path]
        # Set self.waypoints
        self.waypoints = waypoints
        # TODO: send waypoints to sim (this is just for visualization of waypoints)
        self.send_waypoints()

    def start(self):
        self.start_log("Logs", "NavLog.txt")

        print("starting connection")
        self.connection.start()

        # Only required if they do threaded
        # while self.in_mission:
        #    pass

        self.stop_log()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5760, help='Port number')
    parser.add_argument('--host', type=str, default='127.0.0.1', help="host address, i.e. '127.0.0.1'")
    args = parser.parse_args()

    conn = MavlinkConnection('tcp:{0}:{1}'.format(args.host, args.port), timeout=60)
    drone = MotionPlanning(conn)
    time.sleep(1)

    drone.start()