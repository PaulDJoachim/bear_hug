"""
Various useful Widget and Listener classes
These widgets and listeners are usable outside the ECS and should be sufficient
for simpler games and apps. However, for the sake of clearer architecture,
entities are recommended.
"""


import inspect

from bear_hug.bear_hug import BearTerminal
from bear_hug.bear_utilities import shapes_equal, copy_shape,\
    slice_nested, generate_square,\
    BearException, BearLayoutException, BearJSONException
from bear_hug.event import BearEvent

from collections import deque
from json import dumps, loads
from time import time
import numpy as np
from data_structures.tile_types import render_dt, Color, Tile


def deserialize_widget(serial, atlas=None):
    """
    Provided a JSON string, return a widget it encodes.

    Specifics of JSON format are described in the Widget class documentation.
    It is important to know, though, that the Widget subclass that a given JSON
    encodes should be imported to the code that attempts to call this function.

    :param serial: a JSON string or dict
    :returns: a Widget instance
    """

    if isinstance(serial, str):
        d = loads(serial)
    elif isinstance(serial, dict):
        d = serial
    else:
        raise BearJSONException(f'Attempting to deserialize {type(serial)} to Widget')
    for forbidden_key in ('name', 'owner', 'dispatcher'):
        if forbidden_key in d.keys():
            raise BearJSONException(f'Forbidden key {forbidden_key} in widget JSON')
    if 'class' not in d:
        raise BearJSONException('No class provided in component JSON')
    # Only builtins supported for converters. Although custom converters could
    # be provided like with classes, IMO this way is safer
    converters = {}
    for key in d:
        if '_type' in key:
            converters[key[:-5]] = globals()['__builtins__'][d[key]]
    types = [x for x in d if '_type' in x]
    for t in types:
        del(d[t])
    # Try to get the Widget subclass from where the function was imported, or
    # the importers of *that* frame. Without this, the function would only see
    # classes from this very file, or ones imported into it, and that would
    # break the deserialization of custom components.
    class_var = None
    for frame in inspect.getouterframes(inspect.currentframe()):
        if d['class'] in frame.frame.f_globals:
            class_var = frame.frame.f_globals[d['class']]
            break
    del frame
    if not class_var:
        raise BearJSONException(f"Class name {d['class']} not imported anywhere in frame stack")
    if not issubclass(class_var, Widget):
        raise BearJSONException(f"Class name {d['class']}mapped to something other than a Widget subclass")
    kwargs = {}
    for key in d:
        if key in {'class', 'chars', 'colors'}:
            continue
        elif key in converters:
            kwargs[key] = converters[key](d[key])
        elif key == 'animation':
            # animation deserializer will raise exception if atlas is not supplied
            kwargs['animation'] = deserialize_animation(d['animation'], atlas)
        else:
            kwargs[key] = d[key]
    if 'chars' in d:
        # Chars and colors are not kwargs
        return class_var(chars=[[char for char in x] for x in d['chars']],
                         colors=[x.split(',') for x in d['colors']],
                         **kwargs)
    else:
        # Some classes, eg animation widgets, do not dump chars and colors
        return class_var(**kwargs)


def deserialize_animation(serial, atlas=None):
    """

    Deserialize an animation from a JSON dump

    :param serial: A JSON string or a dict.

    :returns: an Animation instance.
    """
    d = loads(serial)
    if d['storage_type'] == 'atlas':
        if not atlas:
            raise BearJSONException('Animation storage type set to atlas, but atlas was not supplied')
        return Animation(frames=[atlas.get_element(x) for x in d['frame_ids']],
                         fps=d['fps'])
    elif d['storage_type'] == 'dump':
        return Animation(frames=[[[[char for char in x] for x in frame[0]],
                                  [x.split(',') for x in frame[1]]]
                                 for frame in d['frames']],
                         fps=d['fps'])
    else:
        raise BearJSONException(f"Incorrect Animation storage_type: {d['storage_type']}")


class Widget:
    """
    The base class for things that can be placed on the terminal.

    This class is inactive and is intended to be either inherited from or used
    for non-interactive non-animated objects. Event processing and animations
    are covered by its subclasses; while it has ``on_event()`` method, it does
    nothing. This allows Widgets to work without subscribing to the queue and
    saves some work on not redrawing them unless the Widget itself considers it
    necessary.

    Under the hood, this class does little more than store a 2D numpy array with
    character and color data.
    
    Widgets can be serialized into JSON similarly to Components and Entities.
    `repr(widget)` is used for serialization and should generate a valid
    JSON-encoded dict. It should always include a ``class`` key which
    should equal the class name for that component and will be used by a
    deserializer to determine what to create. ``chars`` and ``colors` keys are
    also necessary. They should encode widget's chars and colors as arrays of
    strings and each of these strings should be a list of values for
    chars' and colors' inner lists (str-converted chars and str-converted
    `#ffffff`-type colors; comma-separated for colors).
    
    All other keys will be deserialized and treated as kwargs to a newly-created
    object. To define the deserialization protocol, JSON dict may also contain
    keys formatted as ``{kwarg_name}_type``'`` which should be a string and will
    be eval-ed during deserialization. Only Python's builtin converters (eg
    ``str``, ``int`` or ``float``) are safe; custom ones are currently
    unsupported.

    For example, the following is a valid JSON::

        {"class": "MyWidget",
        "chars": ["b,b,b", "a,b,a", "b,a,b"],
        "colors": ["#fff,#fff,#fff", "#000,#fff,#000", "#fff,#000,#fff"],
        "former_owners": ["asd", "zxc", "qwe"],
        "former_owners_type": "set"}

    Its deserialization is equivalent to the following call::

        x = MyWidget(chars=[['bbb'],
                            ['aba'],
                            ['bab']],
                     colors=[['#fff','#fff','#fff'],
                             ['#000','#fff','#000'],
                             ['#fff','#000','#fff']],
                     former_owners=set(['asd, 'zxc', 'qwe']))

    The following keys are forbidden: ``parent`` and ``terminal``. Kwarg
    validity is not controlled except by ``WidgetSubclass.__init__()``.

    :param z_level: a Z-level to determine objects' overlap. Used by (Scrollable)ECSLayout. Not to be mixed up with a terminal layer, these are two independent systems.
    :param font: simple font object, provided by widget. Characters will be drawn in this font instead of default (terminal must be configured with font_of same name).
    """
    def __init__(self, tile_array, layer=0, pos=(0,0), terminal=None, **kwargs):

        self.layer = layer  # the terminal layer to display on
        self.pos = pos
        self.tile_array = tile_array  # numpy array of (char, color) data for display
        self.font_size = kwargs.get('font_size')  # a string specifying terminal font to use, terminal uses default font if None
        self.hidden = False  # skips this widget's tile data when rendering
        self._terminal = terminal  # A widget may want to know about the terminal it's attached to
        self._parent = None  # Or a parent
        self._display = None  # or the display size

    def on_event(self, event):
        # Root widget does not raise anything here, because Widget() can be
        # erroneously subscribed to a queue. While useless, that's not really a
        # fatal error.
        pass
    
    @property
    def terminal(self):
        return self._terminal

    @terminal.setter
    def terminal(self, value):
        if value and not isinstance(value, BearTerminal):
            raise BearException('Only a BearTerminal can be set as ' +
                                'Widget.terminal')
        self._terminal = value

    @property
    def parent(self):
        return self._parent
    
    @parent.setter
    def parent(self, value):
        if value and not isinstance(value, (Widget, BearTerminal)):
            raise BearException(
                'Only a widget or terminal can be a widget\'s parent')
        self._parent = value

    @property
    def display(self):
        return self._display
        
    @property
    def height(self):
        return self.tile_array.shape[0]
    
    @property
    def width(self):
        return self.tile_array.shape[1]
    
    @property
    def size(self):
        return self.tile_array.shape[0], self.tile_array.shape[1]

    def wipe(self):
        """clear all tiles from current widget."""
        self.tile_array['char'] = ' '
        self.terminal.update_widget(self)

    @staticmethod
    def _serialize_charline(charline):
        # TODO consider serialization
        line = ''
        for char in charline:
            if isinstance(char, str):
                line += char
            else:
                line += chr(char)
        return line
            
    def __repr__(self):
        char_strings = [self._serialize_charline(x) for x in self.chars]
        for string in char_strings:
            string.replace('\"', '\u0022"').replace('\\', '\u005c')
        d = {'class': self.__class__.__name__,
             'chars': char_strings,
             'colors': [','.join(x) for x in self.colors],
             'z_level': self.z_level}
        return dumps(d)


class SwitchingWidget(Widget):
    """
    A widget that can contain a collection of chars/colors pairs and switch
    them on command.

    These char/color pairs should all be the same shape. Does not do any
    transition animations.

    ``chars`` and ``colors`` args, although accepted during creation, are
    discarded. They do not affect the created widget in any way, nor are they
    shown at any moment.

    :param images_dict: a dict of {image_id: (chars, colors)}
    :param initial_image: an ID of the first image to show. Should be a key in ``images_dict``.
    """
    
    def __init__(self, chars=None, colors=None,
                 images_dict=None, initial_image=None, **kwargs):
        # Chars and colors are not used anywhere; they are included simply for
        # the compatibility with serialization. Actual chars and colors of the
        # SwitchingWidget are set to `images_dict[initial_image]` upon creation
        test_shape = None
        for image in images_dict:
            # Checking if the image is from JSON (each line is a string) or a
            # correct list-of-lists. If it's former, converts
            if isinstance(images_dict[image][0][0], str):
                images_dict[image][0] = [[char for char in x]
                                         for x in images_dict[image][0]]
                images_dict[image][1] = [list(x.split(','))
                                         for x in images_dict[image][1]]
            if not shapes_equal(images_dict[image][0], images_dict[image][1]):
                raise BearException(
                    f'Chars and colors of different shape for image ID {image} in SwitchingWidget')
            if not test_shape:
                test_shape = (len(images_dict[image][0]),
                              len(images_dict[image][0][0]))
            elif len(images_dict[image][0]) != test_shape[0] or \
                    len(images_dict[image][0][0]) != test_shape[1]:
                raise BearException(
                    f'Image {image} in SwitchingWidget has incorrect size')
        if not initial_image:
            raise BearException('Initial image not set for SwitchingWidget')
        super().__init__(*images_dict[initial_image], **kwargs)
        self.images = images_dict
        self.current_image = initial_image
    
    def switch_to_image(self, image_id):
        """
        Switch to a given image ID

        The ID should be a key in the original ``image_dict``. Otherwise,
        BearException is raised.

        :param image_id: image ID, str.
        """
        if image_id != self.current_image:
            try:
                self.chars = self.images[image_id][0]
                self.colors = self.images[image_id][1]
                self.current_image = image_id
            except KeyError:
                raise BearException(
                    f'Attempting to switch to incorrect image ID {image_id}')
            
    def __repr__(self):
        d = loads(super().__repr__())
        images = {}
        for image in self.images:
            images[image] = []
            images[image].append([self._serialize_charline(x)
                                  for x in self.images[image][0]])
            images[image].append([','.join(x) for x in self.images[image][1]])
        d['images_dict'] = images
        d['initial_image'] = self.current_image
        return dumps(d)
        
        
class Layout(Widget):
    """
    A widget that can add others as its children.

    All children get drawn to its tile array, and are displayed
    within a single bearlibterminal layer. If mulitiple children are overlapping,
    the more recent one will cover the conflicting tiles of the older one.
    The layout does not explicitly pass events to its children,
    they are expected to subscribe to event queue by themselves.

    The Layout is initialized with a single child, which is given chars and
    colors provided at Layout creation. This child is available as
    ``l.children[0]`` or as ``l.background``. Its type is always ``Widget``.

    The Layout automatically redraws itself on `tick` event, whether its
    children have updated or not.
    
    Does not support JSON serialization
    """
    # @profile(immediate=True)
    def __init__(self, tile_array=None, **kwargs):
        super().__init__(tile_array, **kwargs)
        self.children = []  # list of child widget objects (tile arrays)

        # For every position in the tile_array, remember all the child widgets that may want to place
        # characters in it by recording them in an identically shaped boolean array.
        # Each child adds a new boolean mask to the array by expanding the Z layer with values corresponding to position
        # Only the latest addition is actually stored in self.tile_array to be rendered.

        # create a 3d array of bools to hold the layout children position data
        sy, sx = self.tile_array.shape
        self.child_locations = {}  # stores child widgets as keys paired with their location tuple in the layout

        # The widget with Layout's chars and colors is created and added to the
        # Layout as the first child. It is done even if both are empty, just in
        # case someone wants to add background later
        w = Widget(tile_array)
        self.add_child(w, pos=(0, 0))
        self.needs_redraw = False
    
    @property
    def terminal(self):
        return self._terminal
    
    # This setter propagates the terminal value to all the Layout's children.
    # It's necessary because some of them may be added before placing Layout on
    # the screen and thus end up terminal-less.
    @terminal.setter
    def terminal(self, value):
        if value and not isinstance(value, BearTerminal):
            raise BearException('Only BearTerminal can be added as terminal')
        self._terminal = value
        for child in self.children:
            child.terminal = value
        
    # Operations on children
    def add_child(self, child, pos):
        """
        Add a widget as a child at a given position.

        The child has to be a Widget or a Widget subclass that haven't yet been
        added to this Layout and whose dimensions are less than or equal to the
        Layout's. The position is in the Layout coordinates in relation to its
        top left corner.

        :param child: A widget to add.

        :param pos: A widget position, (x, y) 2-tuple
        """
        layout_y, layout_x = self.tile_array.shape
        child_y, child_x = child.tile_array.shape
        pos_y, pos_x = pos
        if not isinstance(child, Widget):
            raise BearLayoutException('Cannot add non-Widget to a Layout')
        if child in self.children:
            raise BearLayoutException('Cannot add the same widget to layout twice')
        if layout_y < child_y or layout_x < child_x:
            raise BearLayoutException('Cannot add child that is bigger than a Layout')
        if child_y + pos_y > layout_y or child_x + pos_x > layout_x:
            raise BearLayoutException('Child won\'t fit at this position')
        if child is self:
            raise BearLayoutException('Cannot add Layout as its own child')

        self.children.append(child)  # add the child to the layout's list of children
        self.child_locations[child] = pos  # add the child's position to the dictionary of positions
        child.terminal = self.terminal
        child.parent = self

        self.needs_redraw = True

    def remove_child(self, child):
        """
        Removes a child from a Layout by deleting its pointer layer and self.children entry

        :param child: the child to remove
        """
        if child not in self.children:
            raise BearLayoutException('Layout can only remove its own child')

        del(self.child_locations[child])
        self.children.remove(child)
        child.terminal = None
        child.parent = None

        self.needs_redraw = True
    
    def move_child(self, child, new_pos):
        """
        Move the child in the layout by altering its location.

        :param child: A child Widget
        :param new_pos: A (y, x) 2-tuple within the layout.
        """
        # TODO add a check to make sure new position is in range
        self.child_locations[child] = new_pos

        self.needs_redraw = True
    
    # BG's chars and colors are not meant to be set directly
    @property
    def background(self):
        return self.children[0]
    
    @background.setter
    def background(self, value):
        # TODO rewrite this to use numpy arrays
        if not isinstance(value, Widget):
            raise BearLayoutException('Only Widget can be added as background')
        if not shapes_equal(self.chars, value.chars):
            # chars and colors are always the same size
            raise BearLayoutException('Wrong Layout background size')
        for row in range(len(self.chars)):
            for column in range(len(self.chars[0])):
                self._child_pointers[row][column][0] = value
        del self.child_locations[self.children[0]]
        self.child_locations[value] = (0, 0)
        self.children[0] = value
        self.needs_redraw = True
        
    def _rebuild_self(self):
        """
        Rebuild the layout's tile_array by iteratively adding and masking each child's tiles in order of newest first
        """
        for child in self.children:
            if not child.hidden:  # if not a hidden child
                child_y, child_x = child.tile_array.shape
                pos_y, pos_x = self.child_locations[child]
                # add child's tiles to layout tile_array
                self.tile_array[pos_y:pos_y + child_y, pos_x: pos_x + child_x] = child.tile_array

    def on_event(self, event):
        """
        Redraw itself, if necessary
        """
        if event.event_type == 'service' and event.event_value == 'tick_over'\
                and self.needs_redraw:
            self._rebuild_self()
            if isinstance(self.parent, BearTerminal):
                self.terminal.update_widget(self)
            self.needs_redraw = False
    
    #Service
    def get_absolute_pos(self, relative_pos):
        """
        Get an absolute position (in terminal coordinates) for any location
        within self.

        :param relative_pos: An (x, y) 2-tuple in Layout coordinates

        :return: An (x, y) 2-tuple for the same point in terminal coordinates.
        """
        self_pos = self.terminal.widget_locations(self).pos
        return self_pos[0]+relative_pos[0], self_pos[1]+relative_pos[1]

    def get_child_on_pos(self, pos, return_bg=False):
        """
        Return the newest child on a given position.

        :param pos: Position in Layout coordinates

        :param return_bg: If True, return background widget when clicking outside any children. If False, return None in this case. Defaults to False

        :return: Widget instance or None
        """
        if len(self._child_pointers[pos[1]][pos[0]]) > 1:
            return self._child_pointers[pos[1]][pos[0]][-1]
        if return_bg:
            return self.background
        else:
            return None

    def __repr__(self):
        raise BearException('Layout does not support __repr__ serialization')


class ScrollBar(Widget):
    """
    A scrollbar to be used with ScrollableLayout.

    Does not accept input, does not support serialization.

    :param orientation: Scrolling direction. One of 'vertical' or 'horizontal'

    :param length: Scrollbar length, in chars.

    :param colors: A 2-tuple of (BG colour, moving thingy colour)
    """
    def __init__(self, orientation='vertical', length=10,
                 colors=('gray', 'white'), **kwargs):
        if orientation not in ('vertical', 'horizontal'):
            raise BearException(
                'Orientation must be either vertical or horizontal')
        if orientation == 'vertical':
            # TODO: custom chars in ScrollBar
            chars = [['#'] for _ in range(length)]
        else:
            chars = [['#' for _ in range(length)]]
        self.length = length
        self.orientation = orientation
        self.bg_color = colors[0]
        self.bar_color = colors[1]
        colors = copy_shape(chars, self.bg_color)
        super().__init__(chars, colors, **kwargs)
        
    def show_pos(self, position, percentage):
        """
        Move the scrollbar.

        :param position: Float. The position of the top (or left) side of the
        scrollbar, as part of its length

        :param percentage: Float. The lengths of the scrollbar, as part of the
        total bar length
        """
        # Not really effective, but still quicker than Layout would be
        # Single-widget bar gets redrawn only when called, while a Layout
        # would've redrawn every tick
        start = round(self.length*position)
        width = round(self.length*percentage)
        self.colors = copy_shape(self.chars, self.bg_color)
        if self.orientation == 'vertical':
            for i in range(start, start+width):
                self.colors[i][0] = self.bar_color
        else:
            for i in range(start, start+width):
                self.colors[0][i] = self.bar_color
                
    def __repr__(self):
        raise BearException('ScrollBar does not support __repr__ serialization')


class ScrollableWidget(Widget):
    """
    A widget that can show only a part ot its tile_array.
    """
    def __init__(self, tile_array, view_pos=(0, 0), view_size=(10, 10), **kwargs):
        if not 0 <= view_pos[0] <= tile_array.shape[0] - view_size[0] \
                or not 0 <= view_pos[1] <= tile_array.shape[1] - view_size[1]:
            raise BearLayoutException('Initial viewpoint outside ScrollableLayout')
        else:
            if not 0 < view_size[0] <= tile_array.shape[0] \
                    or not 0 < view_size[1] <= tile_array.shape[1]:
                raise BearLayoutException('Invalid view field size')

        self.view_pos = view_pos
        self.view_size = view_size  # the size of the viewing area
        self._tile_array = tile_array  # the full tile array for the widget
        # get the viewable slice from the full array
        self.tile_array_view = self._tile_array[self.view_pos[0]:self.view_pos[0] + self.view_size[0],
                                                self.view_pos[1]:self.view_pos[1] + self.view_size[1]]

        super().__init__(tile_array=self.tile_array_view, **kwargs)

    def regenerate_view(self):
        # use the view pos and size to slice the appropriate view from the array
        self.tile_array = self._tile_array[self.view_pos[0]:self.view_pos[0] + self.view_size[0],
                                           self.view_pos[1]:self.view_pos[1] + self.view_size[1]]


class ScrollableLayout(Layout):
    """
    A Layout that can show only a part of its surface.
    
    Like a Layout, accepts a numpy tile_array which should be the
    size of the entire layout, not the visible area. The latter is initialized
    by `view_pos` and `view_size` arguments.
    
    Does not support JSON serialization.

    :param view_pos: a 2-tuple (x,y) for the top left corner of visible area, in Layout coordinates.
    :param view_size: a 2-tuple (width, height) for the size of visible area.
    """
    def __init__(self, tile_array=None, view_pos=(0, 0), view_size=(10, 10), **kwargs):
        super().__init__(tile_array, **kwargs)
        if not 0 <= view_pos[0] <= self.width - view_size[0] \
                or not 0 <= view_pos[1] <= self.height - view_size[1]:
            raise BearLayoutException('Initial viewpoint outside ScrollableLayout')
        else:
            if not 0 < view_size[0] <= tile_array.shape[0] \
                    or not 0 < view_size[1] <= tile_array.shape[1]:
                raise BearLayoutException('Invalid view field size')

        self.view_pos = view_pos[:]
        self.view_size = view_size[:]

        self._rebuild_self()

    def _rebuild_self(self):
        """
        Same as `Layout()._rebuild_self`, but all child positions are also
        offset by `view_pos`. Obviously, only `view_size[1]` lines
        `view_size[0]` long are set as `chars` and `colors`.
        """

        # print(f'from {self.view_pos[1]} to {self.view_pos[1]+self.view_size[1]} and from {self.view_pos[0]} to {self.view_pos[0]+self.view_size[0]}')
        view = self.tile_array[self.view_pos[1]:self.view_pos[1]+self.view_size[1], self.view_pos[0]:self.view_pos[0]+self.view_size[0]]
        chars = view['char'].tolist()
        colors = view['color'].tolist()

        # TODO is this important? Much faster without... not sure what it's doing
        #
        # chars = [[' ' for x in range(self.view_size[0])] \
        #          for y in range(self.view_size[1])]
        # # copies that array
        # colors = copy_shape(chars, None)
        # for line in range(self.view_size[1]):
        #     for char in range(self.view_size[0]):
        #         for child in self._child_pointers[self.view_pos[1]+line] \
        #                              [self.view_pos[0] + char][::-1]:
        #             # Addressing the correct child position
        #             c = child.chars[self.view_pos[1] + line-self.child_locations[child][1]] \
        #                 [self.view_pos[0] + char-self.child_locations[child][0]]
        #             if c != ' ':
        #                 # Spacebars are used as empty space and are transparent
        #                 chars[line][char] = c
        #                 break
        #         colors[line][char] = \
        #             child.colors[self.view_pos[1] + line - self.child_locations[child][1]] \
        #             [self.view_pos[0] + char - self.child_locations[child][0]]

        self.chars = chars
        self.colors = colors

    ##### OLD VERSION
    # def _rebuild_self(self):
    #     """
    #     Same as `Layout()._rebuild_self`, but all child positions are also
    #     offset by `view_pos`. Obviously, only `view_size[1]` lines
    #     `view_size[0]` long are set as `chars` and `colors`.
    #     """
    #     chars = [[' ' for x in range(self.view_size[0])] \
    #              for y in range(self.view_size[1])]
    #     colors = copy_shape(chars, None)
    #     for line in range(self.view_size[1]):
    #         for char in range(self.view_size[0]):
    #             for child in self._child_pointers[self.view_pos[1]+line] \
    #                                  [self.view_pos[0] + char][::-1]:
    #                 # Addressing the correct child position
    #                 c = child.chars[self.view_pos[1] + line-self.child_locations[child][1]] \
    #                     [self.view_pos[0] + char-self.child_locations[child][0]]
    #                 if c != ' ':
    #                     # Spacebars are used as empty space and are transparent
    #                     chars[line][char] = c
    #                     break
    #             colors[line][char] = \
    #                 child.colors[self.view_pos[1] + line - self.child_locations[child][1]] \
    #                 [self.view_pos[0] + char - self.child_locations[child][0]]
    #     self.chars = chars
    #     self.colors = colors

    def resize_view(self, new_size):
        # TODO: support resizing view.
        # This will require updating the pointers in terminal or parent layout
        pass
    
    def scroll_to(self, pos):
        """
        Move field of view to ``pos``.
        
        Raises ``BearLayoutException`` on incorrect position

        :param pos: A 2-tuple of (x, y) in layout coordinates
        """
        if not (len(pos) == 2 and all((isinstance(x, int) for x in pos))):
            raise BearLayoutException('Field of view position should be 2 ints')
        if not 0 <= pos[0] <= len(self._child_pointers[0]) - self.view_size[0] \
                or not 0 <= pos[1] <= len(self._child_pointers)-self.view_size[1]:
            raise BearLayoutException('Scrolling to invalid position')
        self.view_pos = pos

    def scroll_by(self, shift):
        """
        Move field of view by ``shift[0]`` to the right and by ``shift[1]`` down.
        
        Raises ``BearLayoutException`` on incorrect position

        :param shift: A 2-tuple of (dx, dy) in layout coordinates
        """
        pos = (self.view_pos[0] + shift[0], self.view_pos[1] + shift[1])
        self.scroll_to(pos)
    
    def __repr__(self):
        raise BearException('ScrollableLayout does not support __repr__ serialization')
    

class InputScrollable(Layout):
    """
    A ScrollableLayout wrapper that accepts input events and supports the usual
    scrollable view bells and whistles. Like ScrollableLayout, accepts chars and
    colors the size of the *entire* layout and inits visible area using view_pos
    and view_size.
    
    If bottom_bar and/or right_bar is set to True, it will be made one char
    bigger than view_size in the corresponding dimension to add ScrollBar.
    
    Can be scrolled by arrow keys.
    
    Does not support JSON serialization
    """
    def __init__(self, chars, colors, view_pos=(0, 0), view_size=(10, 10),
                 bottom_bar=False, right_bar=False, **kwargs):
        # Scrollable is initalized before self to avoid damaging it by the
        # modified view_size (in case of scrollbars)
        scrollable = ScrollableLayout(chars, colors, view_pos, view_size)
        size = list(view_size)
        ch = slice_nested(chars, view_pos, size)
        co = slice_nested(colors, view_pos, size)
        # Is there something more reasonable to add as ScrollableLayout BG?
        # It shouldn't be shown anyway
        if bottom_bar:
            ch.append(copy_shape(ch[0], ch[0][0]))
            co.append(copy_shape(co[0], co[0][0]))
        if right_bar:
            for x in ch:
                x.append(' ')
            for x in co:
                x.append('white')
        # While True, can add children to self. Otherwise they are passed to
        # self.scrollable
        self.building_self = True
        super().__init__(ch, co, **kwargs)
        self.scrollable = scrollable
        self.add_child(self.scrollable, pos=(0, 0))
        # Need to rebuild now to let bars know the correct height and width
        self._rebuild_self()
        if right_bar:
            self.right_bar = ScrollBar(orientation='vertical',
                                       length=self.height)
            self.add_child(self.right_bar, pos=(self.width-1, 0))
        else:
            self.right_bar = None
        if bottom_bar:
            self.bottom_bar = ScrollBar(orientation='horizontal',
                                        length=self.width)
            self.add_child(self.bottom_bar, pos=(0, self.height-1))
        self.building_self = False
        
    def on_event(self, event):
        if event.event_type == 'key_down':
            scrolled = False
            if event.event_value == 'TK_DOWN' and \
              self.scrollable.view_pos[1] + self.scrollable.view_size[1]\
                    < len(self.scrollable._child_pointers):
                self.scrollable.scroll_by((0, 1))
                scrolled = True
            elif event.event_value == 'TK_UP' and \
             self.scrollable.view_pos[1] > 0:
                self.scrollable.scroll_by((0, -1))
                scrolled = True
            elif event.event_value == 'TK_RIGHT' and \
              self.scrollable.view_pos[0] + self.scrollable.view_size[0]\
                    < len(self.scrollable._child_pointers[0]):
                self.scrollable.scroll_by((1, 0))
                scrolled = True
            elif event.event_value == 'TK_LEFT' and \
              self.scrollable.view_pos[0] > 0:
                self.scrollable.scroll_by((-1, 0))
                scrolled = True
            elif event.event_type == 'TK_SPACE':
                self.scrollable.scroll_to((0, 0))
                scrolled = True
            if scrolled:
                if self.right_bar:
                    self.right_bar.show_pos(
                        self.scrollable.view_pos[1] /
                            len(self.scrollable._child_pointers),
                        self.scrollable.view_size[0] /
                            len(self.scrollable._child_pointers))
        super().on_event(event)

    def add_child(self, child, pos, skip_checks=False):
        if not self.building_self:
            self.scrollable.add_child(child, pos, skip_checks)
        else:
            super().add_child(child, pos, skip_checks)
            
    def __repr__(self):
        raise BearException('InputScrollable does not support __repr__ serialization')
            

# Animations and other complex decorative Widgets
class Animation:
    """
    A data class for animation, *ie* the sequence of the frames
    
    Animation can be serialized to JSON, preserving fps and either frame dumps
    (similarly to widget chars and colors) or frame image IDs. For the latter to
    work, these IDs should be provided during Animation creation via an optional
    ``frame_ids`` kwarg. The deserializer will then use them with whichever atlas
    is supplied to create the animation.
    
    Since this class has no idea of atlases and is unaware whether it was
    created with the same atlas as deserializer will use (which REALLY should be
    the same, doing otherwise is just asking for trouble), frame ID validity is
    not checked until deserialization and, if incorrect, are not guaranteed to
    work.

    :param frames: a list of (chars, colors) tuples

    :param fps: animation speed, in frames per second. If higher than terminal FPS, animation will be shown at terminal FPS.

    :param frame_ids: an optional list of frame names in atlas, to avoid dumping frames. Raises ``BearJSONException`` if its length isn't equal to that of frames.
    """
    def __init__(self, frames, fps, frame_ids=None):
        if not all((shapes_equal(x[0], frames[0][0]) for x in frames[1:])) \
                or not all(
                (shapes_equal(x[1], frames[0][1]) for x in frames[1:])):
            raise BearException('Frames should be equal size')
        if frame_ids:
            if len(frame_ids) != len(frames):
                raise BearJSONException('Incorrect frame_ids length during Animation creation')
            else:
                self.frame_ids = frame_ids
        self.frames = frames
        self.fps = fps # For deserialization
        self.frame_time = 1 / fps

    def __len__(self):
        return len(self.frames)

    @staticmethod
    def _serialize_charline(charline):
        line = ''
        for char in charline:
            if isinstance(char, str):
                line += char
            else:
                line += chr(char)
        return line
    
    def __repr__(self):
        d = {'fps': self.fps}
        if hasattr(self, 'frame_ids'):
            d['storage_type'] = 'atlas'
            d['frame_ids'] = 'frame_ids'
        else:
            frames_dump = []
            for frame in self.frames:
                char_strings = [self._serialize_charline(x) for x in frame[0]]
                for string in char_strings:
                    string.replace('\"', '\u0022"').replace('\\', '\u005c')
                colors_dump = [','.join(x) for x in frame[1]]
                frames_dump.append([char_strings, colors_dump])
            d['frames'] = frames_dump
            d['storage_type'] = 'dump'
        return dumps(d)
        

class SimpleAnimationWidget(Widget):
    """
    A simple animated widget that cycles through the frames.

    :param frames: An iterable of (chars, colors) tuples. These should all be the same size.

    :param fps: Animation speed, in frames per second. If higher than terminal FPS, it will be slowed down.

    :param emit_ecs: If True, emit ecs_update events on every frame. Useless for widgets outside ECS, but those on ``ECSLayout`` are not redrawn unless this event is emitted or something else causes ECSLayout to redraw.
    """
    
    def __init__(self, animation, *args, is_running=True,
                 emit_ecs=True, z_level=0):
        if not isinstance(animation, Animation):
            raise BearException(
                'Only Animation instance can be used in SimpleAnimationWidget')
        self.animation = animation
        super().__init__(*animation.frames[0], *args, z_level=z_level)
        self.running_index = 0
        self.have_waited = 0
        self.emit_ecs = emit_ecs
        self.is_running = is_running
    
    def on_event(self, event):
        if event.event_type == 'tick' and self.is_running:
            self.have_waited += event.event_value
            if self.have_waited >= self.animation.frame_time:
                self.running_index += 1
                if self.running_index >= len(self.animation):
                    self.running_index = 0
                self.chars = self.animation.frames[self.running_index][0]
                self.colors = self.animation.frames[self.running_index][1]
                self.have_waited = 0
                if self.emit_ecs:
                    return BearEvent(event_type='ecs_update')
        elif self.parent is self.terminal and event.event_type == 'service' \
                and event.event_value == 'tick_over':
            # This widget is connected to the terminal directly and must update
            # itself without a layout
            self.terminal.update_widget(self)

    def stop(self):
        self.is_running = False

    def start(self):
        self.is_running = True

    def __repr__(self):
        d = {'class': self.__class__.__name__,
             'animation': repr(self.animation),
             'emit_ecs': self.emit_ecs,
             'is_running': self.is_running,
             'z_level':self.z_level}
        return dumps(d)


class MultipleAnimationWidget(Widget):
    """
    A widget that is able to display multiple animations.

    Plays only one of the animations, unless ordered to change it by
    ``self.set_animation()``

    :param animations: A dict of ``{animation_id: Animation()}``

    :param initial_animation: the animation to start from.

    :param emit_ecs: If True, emit ecs_update events on every frame. Useless for widgets outside ECS, but those on ``ECSLayout`` are not redrawn unless this event is emitted or something else causes the layout to redraw.

    :param cycle: if True, cycles the animation indefinitely. Otherwise stops at the last frame.
    """
    def __init__(self, animations, initial_animation,
                 emit_ecs=True, cycle=False, z_level=0):
        # Check the animations' validity
        if not isinstance(animations, dict) or \
                any((not isinstance(x, Animation) for x in animations.values())):
            raise BearException(
                'Only dict of Animations acceptable for MultipleAnimationWidget')
        if any((not isinstance(x, str) for x in animations)):
            raise BearException('Animation names should be strings')
        if not initial_animation:
            raise BearException('Initial animation ID should be provided')
        if initial_animation not in animations:
            raise BearException('Incorrect initial animation ID')
        super().__init__(*animations[initial_animation].frames[0],
                         z_level=z_level)
        self.animations = animations
        self.current_animation = initial_animation
        self.running_index = 0
        self.have_waited = 0
        self.emit_ecs = emit_ecs
        self.cycle = cycle
        self.am_running = True

    def on_event(self, event):
        # When self.am_running is False, this widget does not respond to any
        # events and acts like a regular passive Widget
        if self.am_running:
            if event.event_type == 'tick':
                self.have_waited += event.event_value
                if self.have_waited >= self.animation.frame_time:
                    self.running_index += 1
                    if self.running_index >= len(self.animation):
                        if self.cycle:
                            self.running_index = 0
                        else:
                            self.am_running = False
                    self.chars = self.animation.frames[self.running_index][0]
                    self.colors = self.animation.frames[self.running_index][1]
                    self.have_waited = 0
                    if self.emit_ecs:
                        return BearEvent(event_type='ecs_update')
            elif self.parent is self.terminal and event.event_type == 'service'\
                    and event.event_value == 'tick_over':
                # This widget is connected to the terminal directly and must
                # update itself without a layout
                self.terminal.update_widget(self)

    @property
    def animation(self):
        return self.animations[self.current_animation]

    def set_animation(self, anim_id, cycle=False):
        """
        Set the next animation to be played.

        :param anim_id: Animation ID. Should be present in self.animations

        :param cycle: Whether to cycle the animation. Default False.
        """
        if anim_id not in self.animations:
            raise BearException('Incorrect animation ID')
        self.current_animation = anim_id
        self.cycle = cycle
        self.am_running = True
        
    def __repr__(self):
        d = {'class': self.__class__.name,
             'animations': {x: repr(self.animations[x])
                            for x in self.animations},
             'initial_animation': self.current_animation,
             'emit_ecs': self.emit_ecs,
             'cycle': self.cycle}
        # Start from whichever animation was displayed during saving
        return dumps(d)


# Functional widgets. Please note that these include no decoration, BG, frame or
# anything else. Ie Label is just a chunk of text on the screen, FPSCounter and
# MousePosWidget are just the numbers that change. For the more complex visuals,
# embed these into a Layout with a preferred BG

class Label(Widget):
    """
    A widget that displays text.

    Accepts only a single string, whether single- or multiline (ie containing
    ``\n`` or not). Does not support any complex text markup. Label's text can be
    edited at any time by setting label.text property. Note that it overwrites
    any changes to ``self.chars`` and ``self.colors`` made after setting
    ``self.text`` the last time.

    Unlike text, Label's height and width cannot be changed. Set these to
    accomodate all possible inputs during Label creation. If a text is too big
    to fit into the Label, ValueError is raised.

    :param text: string to be displayed
    :param just: horizontal text justification, one of 'left', 'right'
    or 'center'. Default 'left'.
    :param color: bearlibterminal-compatible color. Default 'white'
    :param width: text area width. Defaults to the length of the longest ``\n``-delimited substring in ``text``.
    :param height: text area height. Defaults to the line count in `text`
    """
    
    def __init__(self, text, just='left', color='white', bkcolor=0xFF000000, text_width=None, **kwargs):

        self.tile_array = None
        self.text_width = text_width
        self.color = color
        self.bkcolor = bkcolor
        self.just = just
        self.text = text

        super().__init__(self.tile_array, **kwargs)

    @property
    def text(self):
        return self.text

    @text.setter
    def text(self, text):

        lines = text.split('\n')  # split any line breaks
        line_count = len(lines)
        if not self.text_width:
            self.text_width = max(len(x) for x in lines)
        lines = [line.split(' ') for line in lines]  # split every word

        # recalculate the word/line groupings based on available space
        new_lines = []
        for line in lines:
            new_line = ''  # start with empty string
            for word in line:  # add each word while checking if we exceed width
                if len(new_line) > 0:  # if there's already text on this line
                    test_line = new_line + f' {word}'  # add a space before the next word
                else:
                    test_line = new_line + word  # otherwise just add the word
                if len(test_line) <= self.text_width:  # if we're still under allowed length
                    new_line = test_line  # the new line will include the word
                else:
                    new_lines.append(new_line)  # otherwise our line is done
                    new_line = word  # and the unused word begins the next line
            new_lines.append(new_line)

        # create a new array of appropriate size for the width and line count
        tile_array = np.full(shape=(line_count, self.text_width), fill_value=Tile.AIR.value, dtype=render_dt)
        tile_array['color'] = self.color
        tile_array['bkcolor'] = self.bkcolor

        for index in range(len(new_lines)):
            line = new_lines[index]
            if self.just == 'left':
                tile_array['char'][index, ::] = np.fromiter(line.ljust(self.text_width), dtype='U1')
            elif self.just == 'right':
                tile_array['char'][index, ::] = np.fromiter(line.rjust(self.text_width), dtype='U1')
            elif self.just == 'center':
                tile_array['char'][index, ::] = np.fromiter(line.center(self.text_width), dtype='U1')
            else:
                raise BearException("Justification should be 'left', 'right' or 'center'")

        self.tile_array = tile_array
        # MousePosWidgets (a child of Label) may have self.terminal set
        # despite not being connected to the terminal directly
        # if self.terminal and self in self.terminal._widget_pointers:
        #     self.terminal.update_widget(self)

    # def __repr__(self):
    #     d = loads(super().__repr__())
    #     d['text'] = self.text
    #     d['just'] = self.just
    #     d['color'] = self.color
    #     return dumps(d)
            
            
class InputField(Label):
    """
    A single-line field for keyboard input.
    
    The length of the input line is limited by the InputField size. When the
    input is finished (by pressing ENTER), InputField emits a
    ``BearEvent(event_type='text_input', event_value=(field.name, field.text))``

    Since BLT has no support for system keyboard layouts, only supports QWERTY
    Latin. This also applies to non-letter symbols: for example, comma and
    period are considered to be different keys even in Russian layout, where
    they are on the same physical key.
    """
    charcodes = {'SPACE': ' ', 'MINUS': '-', 'EQUALS': '=',
                 'LBRACKET': '[', 'RBRACKET': ']', 'BACKSLASH': '\\',
                 'SEMICOLON': ';', 'APOSTROPHE': '\'', 'GRAVE': '`',
                 'COMMA': ',', 'PERIOD': '.', 'SLASH': '/',
                 'KP_DIVIDE': '/', 'KP_MULTIPLY': '*', 'KP_MINUS': '-',
                 'KP_PLUS': '+', 'KP_1': '1', 'KP_2': '2', 'KP_3': '3',
                 'KP_4': '4', 'KP_5': '5', 'KP_6': 6, 'KP_7': 7,
                 'KP_8': 8, 'KP_9': 9, 'KP_0': 0, 'KP_PERIOD': '.'
                 }
    
    # Charcodes for non-letter characters used via Shift button
    shift_charcodes = {'MINUS': '_', 'EQUALS': '+', 'LBRACKET': '{',
                       'RBRACKET': '}', 'BACKSLASH': '|', 'SEMICOLON': ':',
                       'APOSTROPHE': '\"', 'GRAVE': '~', 'COMMA': '<',
                       'PERIOD': '>', 'SLASH': '?', '1': '!', '2': '@',
                       '3': '#', '4': '$', '5': '%', '6': '^', '7': '&',
                       '8': '*', '9': '(', '0': ')'}
    
    def __init__(self, name='Input field', accept_input=True, finishing=False,
                 **kwargs):
        if 'width' not in kwargs:
            raise BearException('InputField cannot be created without ' +
                                'either `width` or default text')
        super().__init__('', **kwargs)
        # The name will be used when the input is finished
        self.name = name
        self.shift_pressed = False
        # Set to True to return 'text_input' and stop accepting
        self.finishing = finishing
        self.accept_input = accept_input
        
    def on_event(self, event):
        #TODO Reactivate InputField on mouse click, if inactive
        # Requires it to have terminal for state.
        if self.finishing:
            # If finishing, the event will be ignored
            return BearEvent(event_type='text_input',
                             event_value=(self.name, self.text))
        if self.accept_input and event.event_type == 'key_down':
            # Stripping 'TK_' part
            symbol = event.event_value[3:]
            if symbol == 'BACKSPACE':
                self.text = self.text[:-1]
            elif symbol == 'SHIFT':
                self.shift_pressed = True
            # TK_ENTER is presumed to be the end of input
            elif symbol == 'ENTER':
                self.accept_input = False
                # returns immediately, unlike self.finish which sets it to
                # return on the *next* event
                return BearEvent(event_type='text_input',
                                 event_value=(self.name, self.text))
            elif len(self.text) < len(self.chars[0]):
                self.text += self._get_char(symbol)
            if self.terminal:
                self.terminal.update_widget(self)
        elif event.event_type == 'key_up':
            if event.event_value == 'TK_SHIFT':
                self.shift_pressed = False

    def finish(self):
        """
        Finish accepting the input and emit the 'text_input' event at the next
        opportunity. This opportunity will not present itself until the next
        event is passed to ``self.on_event``.
        """
        self.accept_input = False
        self.finishing = True
        
    def _get_char(self, symbol):
        """
        Return the char corresponding to a TK_* code.
        
        Considers the shift state
        :param symbol:
        :return:
        """
        if len(symbol) == 1:
            if self.shift_pressed:
                if symbol in '1234567890':
                    return self.shift_charcodes[symbol]
                else:
                    return symbol
            else:
                return symbol.lower()
        elif symbol in self.charcodes:
            if self.shift_pressed and symbol in self.shift_charcodes:
                return self.shift_charcodes[symbol]
            else:
                return self.charcodes[symbol]
        else:
            return ''
        
    def __repr__(self):
        d = loads(super().__repr__())
        d['name'] = self.name
        d['finishing'] = self.finishing
        d['accept_input'] = self.accept_input
        return dumps(d)


class MenuWidget(Layout):
    """
    A menu widget that includes multiple buttons.

    :param dispatcher: BearEventDispatcher instance to which the menu will subscribe
    :param items: an iterable of MenuItems
    :param background: A background widget for the menu. If not supplied, a default double-thickness box is used. If background widget needs to get events (ie for animation), it should be subscribed by the time it's passed here.
    :param color: A bearlibterminal-compatible color. Used for a menu frame and header text
    :param items_pos: A 2-tuple of ints. A position of top-left corner of the 1st MenuItem
    :param header: str or None. A menu header. This should not be longer than menu width, otherwise an exception is thrown. Header may look ugly with custom backgrounds, since it's only intended for non-custom menus.
    :param switch_sound: str. A sound which should be played (via ``play_sound`` BearEvent) when a button is highlighted.
    :param activation_sound: str. A sound which should be played (vai ``play_sound`` BearEvent) when a button is pressed
    """
    def __init__(self, dispatcher, terminal=None, items=[], header=None,
                 color=Color.WHITE,
                 bkcolor=0xFF000000,
                 items_pos=(2, 2),
                 switch_sound=None,
                 activation_sound=None,
                 display=None,
                 **kwargs):
        self.items = []
        self.dispatcher = dispatcher
        self.color = color
        self.bkcolor = bkcolor
        self.items_pos = items_pos
        height = len(items) * 3 + 4
        width = max([item.tile_array.shape[1] for item in items]) + 4
        menu_array = generate_square((height, width), 'double')
        menu_array['color'] = self.color
        menu_array['bkcolor'] = self.bkcolor
        menu_array[1:-1, 1:-1]['char'] = '█'
        menu_array[1:-1, 1:-1]['color'] = 0x00000000
        pos = (display.tiles_x_count-width * 2)//2, (display.tiles_y_count-height * 2)//2

        super().__init__(tile_array=menu_array, terminal=terminal, pos=pos, **kwargs)

        # if terminal and not isinstance(terminal, BearTerminal):
        #     raise TypeError(f'{type(terminal)} used as a terminal for MenuWidget instead of BearTerminal')
        # self.terminal = terminal

        for item in items:  # add all the menu buttons
            self.add_child(item, items_pos)
            items_pos = (items_pos[0] + 3, items_pos[1])

        # Adding buttons
        current_height = items_pos[0]
        for item in self.items:
            self.add_child(item, (current_height, items_pos[1]))
            current_height += item.height + 1

        # Adding header, if any
        if header:
            if not isinstance(header, str):
                raise TypeError(f'{type(header)} used instead of string for MenuWidget header')
            if len(header) > menu_array.shape[1] - 2:
                raise BearLayoutException(f'MenuWidget header is too long')
            header_label = Label(header, color=self.color, bkcolor=self.bkcolor)
            x = (menu_array.shape[1] - header_label.width) // 2
            self.add_child(header_label, (0, x))

        # Prevent scrolling multiple times when key is pressed
        self.input_delay = 0.2
        self.current_delay = self.input_delay
        self._current_highlight = 1
        # Storing sounds
        self.switch_sound = switch_sound
        self.activation_sound = activation_sound
        self.children[self.current_highlight].highlight()

    @property
    def current_highlight(self):
        return self._current_highlight

    @current_highlight.setter
    def current_highlight(self, value):
        if not 1 <= value <= len(self.children) - 1:
            raise ValueError('current_highlight can only be set to a valid item index')
        self.children[self._current_highlight].unhighlight()
        self._current_highlight = value
        self.children[self._current_highlight].highlight()
        self.needs_redraw = True

    def on_event(self, event):
        r = None
        have_switched = False
        have_activated = False
        if event.event_type == 'tick' and self.current_delay <= self.input_delay:
            self.current_delay += event.event_value
        elif event.event_type == 'key_down' and self.current_delay >= self.input_delay:
            self.current_delay = 0
            print('PRESSED:', event.event_value)
            if event.event_value in ('TK_SPACE', 'TK_ENTER'):
                have_activated = True
                r = self.children[self.current_highlight].activate()
            elif event.event_value in ('TK_UP', 'TK_W') \
                    and self.current_highlight > 1:
                have_switched = True
                self.current_highlight -= 1
            elif event.event_value in ('TK_DOWN', 'TK_S') \
                    and self.current_highlight < len(self.children) - 2:
                have_switched = True
                self.current_highlight += 1
            elif event.event_value == 'TK_MOUSE_LEFT':
                pass
                # TODO enable mouse input?
                # if self.terminal:
                #     # Silently ignore mouse input if terminal is not set
                #     mouse_x = self.terminal.check_state('TK_MOUSE_X')
                #     mouse_y = self.terminal.check_state('TK_MOUSE_Y')
                #     x, y = self.terminal.widget_locations[self].pos
                #     if x <= mouse_x <= x + self.width and y <= mouse_y <= y + self.height:
                #         b = self.get_child_on_pos((mouse_x - x, mouse_y -y))
                #         # self.current_highlight = self.items.index(b)
                #         if isinstance(b, MenuItem):
                #             have_activated = True
                #             r = self.children[self.current_highlight].activate()
        elif event.event_type == 'misc_input' and event.event_value == 'TK_MOUSE_MOVE':
            if self.terminal:
                # Silently ignore mouse input if terminal is not set
                mouse_x = self.terminal.check_state('TK_MOUSE_X')
                mouse_y = self.terminal.check_state('TK_MOUSE_Y')
                x, y = self.terminal.widget_locations[self].pos
                if x <= mouse_x < x + self.width and y <= mouse_y < y + self.height:
                    b = self.get_child_on_pos((mouse_x - x, mouse_y - y))
                    # Could be the menu header
                    if isinstance(b, MenuItem):
                        have_switched = True
                        self.current_highlight = self.children.index(b)
        # Whatever type r was, convert it into a (possibly empty) list of BearEvents
        ret = []
        if r:
            if isinstance(r, BearEvent):
                ret = [r]
            else:
                for e in r:
                    if isinstance(e, BearEvent):
                        ret.append(e)
                    else:
                        raise TypeError(f'MenuItem action returned {type(e)} instead of a BearEvent')
        else:
            ret = []
        for item in self.children:
            # Pass all events to items. Necessary for correct redrawing (ie
            # setting need_redraw on MenuItem instances after (de)highlighting),
            # could be useful otherwise.
            response = item.on_event(event)
            if response:
                ret.append(item)
        if self.switch_sound and have_switched:
            ret.append(BearEvent('play_sound', self.switch_sound))
        if self.activation_sound and have_activated:
            ret.append(BearEvent('play_sound', self.activation_sound))
        s = super().on_event(event)
        if s:
            if isinstance(s, BearEvent):
                ret.append(s)
            else:
                for e in s:
                    if isinstance(e, BearEvent):
                        ret.append(e)
                    else:
                        raise TypeError(
                            f'Layout on_event returned {type(e)} instead of a BearEvent')
        return ret


class MenuItem(Widget):
    """
    A button for use inside menus. Includes a label surrounded by a single-width
    box. Contains a single callable, ``self.action``, which will be called when
    this button is activated.

    MenuItem by itself does not handle any input. It provides ``self.activate``
    method which should be called by something (presumably a menu containing
    this button).

    :param text: str. A button label

    :param action: callable. An action that this MenuItem performs. This should return either None, BearEvent or an iterable of BearEvents

    :param color: a bearlibterminal-compatible color that this button has by
    default

    :param highlight_color: a bearlibterminal-compatible color that this button
    has when highlighted via keyboard menu choice or mouse hover.
    """
    def __init__(self, text='Test', action=lambda: print('Button pressed'),
                 color='white', highlight_color='green',
                 **kwargs):
        self.color = color
        self.highlight_color = highlight_color
        # Widget generation
        label = Label(text, color=self.color)
        self.tile_array = generate_square((label.height+2, label.width+2), 'single', color)
        self.tile_array[1, 1:label.width + 1] = label.tile_array
        super().__init__(self.tile_array)

        if not hasattr(action, '__call__'):
            raise BearException('Action for a button should be callable')
        self.action = action

    def highlight(self):
        """
        Change button colors to show that it's highlighted
        """
        self.tile_array['color'] = self.highlight_color
        self.needs_redraw = True

    def unhighlight(self):
        """
        Change button colors to show that it's no longer highlighted
        :return:
        """
        self.tile_array['color'] = self.color
        self.needs_redraw = True

    def activate(self):
        """
        Perform the button's action
        """
        return self.action()


class FPSCounter(Label):
    """
    A simple widget that measures FPS.

    Actually just prints 1/(average runtime over the last 100 ticks in seconds),
    so it takes 100 ticks to get an accurate reading. Not relevant except on the
    first several seconds of the program run or after FPS has changed, but if it
    seems like the game takes a second or two to reach the target FPS -- it just
    seems that way.
    """
    def __init__(self, **kwargs):
        self.samples_deque = deque(maxlen=100)
        super().__init__('030', **kwargs)
    
    def _update_self(self):
        fps = str(round(len(self.samples_deque) /
                            sum(self.samples_deque)))
        fps = fps.rjust(3, '0')
        self.text = fps
    
    def on_event(self, event):
        # Update FPS estimate
        if event.event_type == 'tick':
            self.samples_deque.append(event.event_value)
            self._update_self()
            if self.parent is self.terminal:
                self.terminal.update_widget(self, refresh=True)
                
    def __repr__(self):
        raise BearException('FPSCounter does not support __repr__ serialization')
        # This should only be used **OUTSIDE** the ECS system.
        # Some debug screen or something


class MousePosWidget(Label):
    """
    A simple widget that reports current mouse position.
    

    In order to work, it needs ``self.terminal`` to be set to the current
    terminal, which means it should either be added to the terminal directly
    (without any Layouts) or terminal should be set manually before
    MousePosWidget gets its first ``tick`` event. It is also important that this
    class uses ``misc_input``:``TK_MOUSE_MOVE`` events to determine mouse
    position, so it would report a default value of '000x000' until the mouse
    has moved at least once.
    """
    
    def __init__(self, **kwargs):
        super().__init__(text='000x000', **kwargs)
        
    def on_event(self, event):
        if event.event_type == 'misc_input' and \
                     event.event_value == 'TK_MOUSE_MOVE':
            self.text = self._get_mouse_line()
        if isinstance(self.parent, BearTerminal):
            self.terminal.update_widget(self)

    def _get_mouse_line(self):
        if not self.terminal:
            raise BearException('MousePosWidget is not connected to a terminal')
        x = str(self.terminal.check_state('TK_MOUSE_X')).rjust(3, '0')
        y = str(self.terminal.check_state('TK_MOUSE_Y')).rjust(3, '0')
        return x + 'x' + y

    def __repr__(self):
        raise BearException('MousePosWidget does not support __repr__ serialization')
    
# Listeners


class Listener:
    """
    A base class for the things that need to interact with the queue (and maybe
    the terminal), but aren't Widgets.

    :param terminal: BearTerminal instance
    """
    def __init__(self, terminal=None):
        if terminal is not None:
            self.register_terminal(terminal)
    
    def on_event(self, event):
        """
        The event callback. This should be overridden by child classes.

        :param event: BearEvent instance
        """
        raise NotImplementedError('Listener base class is doing nothing')
    
    def register_terminal(self, terminal):
        """
        Register a terminal with which this listener will interact

        :param terminal: A BearTerminal instance
        """
        if not isinstance(terminal, BearTerminal):
            raise TypeError('Only BearTerminal instances registered by Listener')
        self.terminal = terminal
        
    
class ClosingListener(Listener):
    """
    The listener that waits for a ``TK_CLOSE`` input event (Alt-F4 or closing
    window) and sends the shutdown service event to the queue when it gets one.

    All widgets are expected to listen to it and immediately save their data or
    do whatever they need to do about it. On the next tick ClosingListener
    closes both terminal and queue altogether.
    """
    def __init__(self):
        super().__init__()
        self.countdown = 2
        self.counting = False
        
    def on_event(self, event):
        if event.event_type == 'misc_input' and event.event_value == 'TK_CLOSE':
            self.counting = True
            return BearEvent(event_type='service', event_value='shutdown_ready')
        if event.event_type == 'tick':
            if self.counting:
                self.countdown -= 1
                if self.countdown == 0:
                    return BearEvent(event_type='service',
                                     event_value='shutdown')


class LoggingListener(Listener):
    """
    A listener that logs the events it gets.

    It just prints whatever events it gets to sys.stderr. The correct
    way to use this class is to subscribe an instance to the events of interest
    and watch the output. If logging non-builtin events, make sure that their
    ``event_value`` can be converted to a string. Converstion uses
    ``str(value)``, not ``repr(value)`` to avoid dumping entire JSON representations.
    """
    def __init__(self, handle):
        super().__init__()
        if not hasattr(handle, 'write'):
            raise BearException('The LoggingListener needs a writable object')
        self.handle = handle
        
    def on_event(self, event):
        self.handle.write('{0}: type {1}, '.format(str(time()), event.event_type) +
                          'value {}\n'.format(event.event_value))
