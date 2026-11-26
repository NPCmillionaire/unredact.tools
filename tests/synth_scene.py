"""Synthetic camera + sun scene for testing shadow measurement."""
import math

import numpy as np


class Scene:
    def __init__(self, w=4000, h=3000, f35=26, yaw=30, pitch=8, roll=1.5, cam_h=1.6,
                 sun_el=22.8, sun_az=217):
        self.w, self.h, self.f35 = w, h, f35
        self.f = f35 / 43.27 * math.hypot(w, h)
        y, p, r = (math.radians(v) for v in (yaw, pitch, roll))
        fwd = np.array([math.sin(y) * math.cos(p), math.cos(y) * math.cos(p), -math.sin(p)])
        right0 = np.array([math.cos(y), -math.sin(y), 0.0])
        down0 = np.cross(fwd, right0)
        self.right = right0 * math.cos(r) + down0 * math.sin(r)
        self.down = -right0 * math.sin(r) + down0 * math.cos(r)
        self.fwd, self.C, self.yaw = fwd, np.array([0, 0, cam_h]), yaw
        e, a = math.radians(sun_el), math.radians(sun_az)
        self.to_sun = np.array([math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)])

    def project(self, P):
        d = np.asarray(P, float) - self.C
        x, yv, z = d @ self.right, d @ self.down, d @ self.fwd
        return (self.w / 2 + self.f * x / z, self.h / 2 + self.f * yv / z)

    def pole(self, dist, side, height=2.0, lean=(0, 0)):
        """Pole `dist` m ahead and `side` m to the right; returns (base, top, tip) pixels."""
        yr = math.radians(self.yaw)
        base = np.array([dist * math.sin(yr) + side * math.cos(yr),
                         dist * math.cos(yr) - side * math.sin(yr), 0.0])
        top = base + np.array([lean[0], lean[1], height])
        tip = top - self.to_sun * (top[2] / self.to_sun[2])
        return self.project(base), self.project(top), self.project(tip)

    def horizon(self):
        yr = math.radians(self.yaw)
        far = 1e7
        a = np.array([far * math.sin(yr - 0.3), far * math.cos(yr - 0.3), 0])
        b = np.array([far * math.sin(yr + 0.3), far * math.cos(yr + 0.3), 0])
        return self.project(a), self.project(b)
