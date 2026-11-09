"""Template for a raid with mechanics the generic runner cannot express.

Set "module": "example_custom" in a profile to use it. Copy this file, rename it, and
override only the hooks you need (enter, approach_boss, handle_death, fight, claim, restart).
"""
from raids.base import Raid


class StrafingRaid(Raid):
    """Example: sidestep after approaching, for bosses with a frontal attack."""

    def approach_boss(self):
        super().approach_boss()
        strafe = self.combat.get("strafe_key")
        if strafe:
            self.rt.input.tap(strafe, float(self.combat.get("strafe_seconds", 0.3)), self.rt.stopping)


RAID_CLASS = StrafingRaid
