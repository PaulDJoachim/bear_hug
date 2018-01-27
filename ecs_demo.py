#! /usr/bin/env python3.6

"""
An ECS test.
"""
from bear_hug import BearTerminal, BearLoop
from bear_utilities import copy_shape
from ecs import Entity, WidgetComponent, PositionComponent, SpawnerComponent
from ecs_widgets import ECSLayout
from event import BearEventDispatcher, BearEvent
from resources import Atlas, XpLoader
from widgets import ClosingListener, Widget, FPSCounter, MousePosWidget, Layout


class DevMonitor(Layout):
    """
    A monitor that shows FPS and mouse position
    Doesn't do any tracking by itself
    """
    
    def __init__(self, chars, colors, dispatcher):
        super().__init__(chars, colors)
        counter = FPSCounter()
        dispatcher.register_listener(counter, 'tick')
        self.add_child(counter, (2, 4))
        # Have to remember mouser for terminal setter
        self.mouser = MousePosWidget()
        dispatcher.register_listener(self.mouser, ['tick', 'misc_input'])
        self.add_child(self.mouser, (2, 7))
    
    @property
    def terminal(self):
        return self._terminal
    
    @terminal.setter
    def terminal(self, value):
        self.mouser.terminal = value
        self._terminal = value
        

class WalkerComponent(PositionComponent):
    """
    A simple PositionComponent that can change x;y on keypress
    """
    def on_event(self, event):
        if event.event_type == 'key_down':
            moved = False
            if event.event_value in ('TK_D', 'TK_RIGHT'):
                self.move(self.x + 1, self.y)
            elif event.event_value in ('TK_A', 'TK_LEFT'):
                self.move(self.x - 1, self.y)
            elif event.event_value in ('TK_S', 'TK_DOWN'):
                self.relative_move(0, 1)
            elif event.event_value in ('TK_W', 'TK_UP'):
                self.relative_move(0, 1)
            elif event.event_value in ('TK_SPACE'):
                self.owner.spawner.create_entity()
            if moved:
                return BearEvent(event_type='ecs_move',
                                 event_value=(self.owner.id, self.x, self.y))
        super().on_event(event)


def create_bullet():
    """
    Create a bullet
    :return:
    """
    atlas = Atlas(XpLoader('test_atlas.xp'), 'test_atlas.json')
    bullet_entity = Entity(id='bullet')
    widget = Widget(*atlas.get_element('bullet'))
    widget_component = WidgetComponent(None, widget, owner=bullet_entity)
    dispatcher.register_listener(widget_component, 'tick')
    position = PositionComponent(None, owner=bullet_entity)
    dispatcher.register_listener(position, 'tick')
    return bullet_entity
    

def create_cop(atlas, dispatcher, x, y):
    """
    Create a cop entity
    :param dispatcher:
    :return:
    """
    punk_entity = Entity(id='cop')
    widget = Widget(*atlas.get_element('cop'))
    widget_component = WidgetComponent(dispatcher, widget, owner=punk_entity)
    dispatcher.register_listener(widget_component, 'tick')
    position_component = WalkerComponent(dispatcher, x=x, y=y,
                                         owner=punk_entity)
    dispatcher.register_listener(position_component, ['tick', 'key_down'])
    spawner = SpawnerComponent(dispatcher, create_bullet,
                               relative_pos=(13, 4),
                               owner=punk_entity)
    dispatcher.add_event(BearEvent(event_type='ecs_create',
                                   event_value=punk_entity))
    dispatcher.add_event(BearEvent(event_type='ecs_add',
                                   event_value=('cop', x, y)))

    
t = BearTerminal(size='85x60', title='Test window',
                 filter=['keyboard', 'mouse'])
dispatcher = BearEventDispatcher()
loop = BearLoop(t, dispatcher)
dispatcher.register_listener(ClosingListener(), ['misc_input', 'tick'])
atlas = Atlas(XpLoader('test_atlas.xp'), 'test_atlas.json')
chars = [['.' for x in range(85)] for y in range(50)]
colors = copy_shape(chars, 'gray')
layout = ECSLayout(chars, colors)
dispatcher.register_listener(layout, 'all')

create_cop(atlas, dispatcher, 5, 5)

# Dev monitor, works outside ECS
monitor = DevMonitor(*atlas.get_element('dev_bg'), dispatcher)
dispatcher.register_listener(monitor, ['tick', 'service'])

t.start()
t.add_widget(monitor, (0, 50), layer=1)
t.add_widget(layout, (0, 0), layer=1)
loop.run()


