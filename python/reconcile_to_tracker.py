# Reconcile3D -> Tracker4 (baked)
#
# Select any number of Reconcile3D nodes and run `reconcile_to_tracker()`. It
# creates one Tracker4 with one track per Reconcile3D, with track_x / track_y
# BAKED as keyframes from that node's XY output over the frame range, so the
# Tracker's Export > "Transform (match-move, baked)" has animation to work from.
#
# Tracks are named after the Axis feeding each Reconcile3D (Axis1, Axis2, ...),
# falling back to the Reconcile3D's own name.
#
# Usage:
#     reconcile_to_tracker()                      # project frame range
#     reconcile_to_tracker(first=1001, last=1402) # custom range
#     reconcile_to_tracker(link=True)             # live expressions instead of keys
#
# Target: Nuke 16 (Tracker4 `tracks` table: 31 columns, track_x = column 2,
# track_y = column 3). Tracker4's `add_track` button does not add rows when
# executed from Python, so the table is written directly with fromScript().
#
# Install: put this file on the plugin path and add to menu.py:
#
#     import reconcile_to_tracker
#     nuke.menu('Nuke').addCommand('Custom/Reconcile3D to Tracker',
#                                  'reconcile_to_tracker.reconcile_to_tracker()')
#
# Or paste it into the Script Editor and call reconcile_to_tracker().
import nuke

_NCOL = 31          # columns per track row in Tracker4's `tracks` table
_COL_X, _COL_Y = 2, 3


def _track_name(rec):
    for i in range(rec.inputs()):
        src = rec.input(i)
        if src and src.Class().startswith('Axis'):
            return src.name()
    return rec.name()


def _bake_reconcile(rec, first, last):
    """Key rec's `output` (XY) and `outputxyz` over first..last, calc_output off."""
    knobs = [rec['output'], rec['outputxyz']]
    for k in knobs:
        k.clearAnimated()
    rec['calc_output'].setValue(True)
    frames = range(first, last + 1)
    vals = [[[k.getValueAt(f, i) for i in range(k.arraySize())] for f in frames]
            for k in knobs]
    rec['calc_output'].setValue(False)
    for k, per_frame in zip(knobs, vals):
        k.setAnimated()
        for f, v in zip(frames, per_frame):
            for i, x in enumerate(v):
                k.setValueAt(x, f, i)


def reconcile_to_tracker(nodes=None, first=None, last=None, link=False):
    recs = sorted(nodes or nuke.selectedNodes('Reconcile3D'),
                  key=lambda n: n.name())
    if not recs:
        nuke.message('Select one or more Reconcile3D nodes first.')
        return None
    first = int(nuke.root().firstFrame() if first is None else first)
    last = int(nuke.root().lastFrame() if last is None else last)

    # Step 1: bake keyframes on each Reconcile3D (what "create keyframes" does).
    # Reconcile3D only computes output live while calc_output is on, so read
    # every frame with it on, then turn it off and write the keys.
    if not link:
        for rec in recs:
            _bake_reconcile(rec, first, last)

    # Step 2: copy the baked keyframes into the tracker.
    data = {}
    if not link:
        for rec in recs:
            out = rec['output']
            data[rec] = [(f, out.getValueAt(f, 0), out.getValueAt(f, 1))
                         for f in range(first, last + 1)]

    for n in nuke.selectedNodes():
        n.setSelected(False)
    tracker = nuke.createNode('Tracker4', inpanel=False)
    tracker.setName('ReconcileTracks')
    tracker.setXYpos(recs[0].xpos(), recs[0].ypos() + 150)

    # Reuse the column definitions Nuke generated for this knob.
    tr = tracker['tracks']
    script = tr.toScript()
    rest = script.split('\n', 1)[1]
    colblock = rest[:rest.index('} \n{ \n}') + 1]

    def curve(vals):
        # one key per frame, starting at `first`
        return '{curve x%d %s}' % (first, ' '.join('%.6f' % v for v in vals))

    rows = []
    for rec in recs:
        if link:
            tx = ty = '0'
        else:
            tx = curve([x for _, x, _ in data[rec]])
            ty = curve([y for _, _, y in data[rec]])
        # enable, name, track_x, track_y, offset_x, offset_y, T, R, S, error, ...
        cells = (['1', '"%s"' % _track_name(rec)] + [tx, ty] + ['0', '0']
                 + ['1', '1', '1'] + ['0'] * (_NCOL - 9))
        rows.append('{ %s }' % ' '.join(cells))
    tr.fromScript('{ 1 %d %d } \n%s \n{ \n%s \n} \n'
                  % (_NCOL, len(recs), colblock, '\n'.join(rows)))

    if link:
        for i, rec in enumerate(recs):
            tr.setExpression('%s.output.x' % rec.name(), i * _NCOL + _COL_X)
            tr.setExpression('%s.output.y' % rec.name(), i * _NCOL + _COL_Y)

    print('ReconcileTracks: %d tracks %s (%d-%d)'
          % (len(recs), 'linked' if link else 'baked', first, last))
    return tracker
