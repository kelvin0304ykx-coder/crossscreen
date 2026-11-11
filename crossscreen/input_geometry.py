from dataclasses import dataclass


@dataclass(frozen=True)
class Display:
    x: int
    y: int
    width: int
    height: int

    def clamp(self, x, y):
        return (max(self.x, min(self.x+self.width-1, round(x))),
                max(self.y, min(self.y+self.height-1, round(y))))

    def ratio(self, y):
        return max(0., min(1., (y-self.y) / max(1, self.height-1)))

    def entry(self, edge, ratio):
        inset = min(8, self.width // 4)
        x = self.x + inset if edge == 'left' else self.x + self.width - 1 - inset
        return self.clamp(x, self.y + ratio*(self.height-1))

    def crossed(self, edge, x, y, dx, dy):
        if not self.y <= y < self.y+self.height:
            return False
        return ((edge == 'left' and x <= self.x+1 and dx < 0) or
                (edge == 'right' and x >= self.x+self.width-2 and dx > 0))
