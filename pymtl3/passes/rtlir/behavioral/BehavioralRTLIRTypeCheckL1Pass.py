#=========================================================================
# BehavioralRTLIRTypeCheckL1Pass.py
#=========================================================================
# Author : Peitian Pan
# Date   : March 20, 2019
"""Provide L1 behavioral RTLIR type check pass."""
from __future__ import absolute_import, division, print_function

import copy

from pymtl3.datatypes import Bits32, mk_bits
from pymtl3.passes.BasePass import BasePass, PassMetadata
from pymtl3.passes.rtlir.errors import PyMTLTypeError
from pymtl3.passes.rtlir.rtype import RTLIRDataType as rdt
from pymtl3.passes.rtlir.rtype import RTLIRType as rt

from . import BehavioralRTLIR as bir


class BehavioralRTLIRTypeCheckL1Pass( BasePass ):
  def __call__( s, m ):
    """Perform type checking on all RTLIR in rtlir_upblks."""
    if not hasattr( m, '_pass_behavioral_rtlir_type_check' ):
      m._pass_behavioral_rtlir_type_check = PassMetadata()
    m._pass_behavioral_rtlir_type_check.rtlir_freevars = {}
    visitor = BehavioralRTLIRTypeCheckVisitorL1(
      m, m._pass_behavioral_rtlir_type_check.rtlir_freevars )
    for blk in m.get_update_blocks():
      visitor.enter( blk, m._pass_behavioral_rtlir_gen.rtlir_upblks[ blk ] )

class BehavioralRTLIRTypeCheckVisitorL1( bir.BehavioralRTLIRNodeVisitor ):
  def __init__( s, component, freevars ):
    s.component = component
    s.freevars = freevars
    s.type_expect = {}
    lhs_types = ( rt.Port, rt.Wire )
    index_types = ( rt.Port, rt.Wire, rt.Array )
    s.type_expect[ 'Assign' ] = {
      'target' : ( lhs_types, 'lhs of assignment must be a signal!' ),
      'value' : ( rt.Signal, 'rhs of assignment should be signal or const!' )
    }
    s.type_expect[ 'ZeroExt' ] = {
      'value':( rt.Signal, 'extension only applies to signals!' )
    }
    s.type_expect[ 'SignExt' ] = {
      'value':( rt.Signal, 'extension only applies to signals!' )
    }
    s.type_expect[ 'SizeCast' ] = {
      'value':( rt.Signal, 'size casting only applies to signals/consts!' )
    }
    s.type_expect[ 'Attribute' ] = {
      'value':( rt.Component, 'the base of an attribute must be a component!' )
    }
    s.type_expect[ 'Index' ] = {
      'idx':(rt.Signal, 'index must be a signal or constant expression!'),
      'value':(index_types, 'the base of an index must be an array or signal!')
    }
    s.type_expect[ 'Slice' ] = {
      'value':( lhs_types, 'the base of a slice must be a signal!' ),
      'lower':( rt.Signal, 'upper of slice must be a constant expression!' ),
      'upper':( rt.Signal, 'lower of slice must be a constant expression!' )
    }

  def enter( s, blk, rtlir ):
    """ entry point for RTLIR type checking """
    s.blk     = blk

    # s.globals contains a dict of the global namespace of the module where
    # blk was defined
    s.globals = blk.__globals__

    # s.closure contains the free variables defined in an enclosing scope.
    # Basically this is the model instance s.
    s.closure = {}

    for i, var in enumerate( blk.__code__.co_freevars ):
      try:
        s.closure[ var ] = blk.__closure__[ i ].cell_contents
      except ValueError:
        pass
    s.visit( rtlir )

  # Override the default visit()
  def visit( s, node ):
    node_name = node.__class__.__name__
    method = 'visit_' + node_name
    func = getattr( s, method, s.generic_visit )

    # First visit (type check) all child nodes
    for field, value in vars(node).iteritems():
      if isinstance( value, list ):
        for item in value:
          if isinstance( item, bir.BaseBehavioralRTLIR ):
            s.visit( item )
      elif isinstance( value, bir.BaseBehavioralRTLIR ):
        s.visit( value )

    # Then verify that all child nodes have desired types
    try:
      # Check the expected types of child nodes
      for field, type_rule in s.type_expect[node_name].iteritems():
        value = vars(node)[field]
        target_type = type_rule[ 0 ]
        exception_msg = type_rule[ 1 ]
        if eval( 'not isinstance( value.Type, target_type )' ):
          raise PyMTLTypeError( s.blk, node.ast, exception_msg )
    except PyMTLTypeError:
      raise
    except Exception:
      # This node does not require type checking on child nodes
      pass

    # Finally call the type check function
    func( node )

  # Override the default generic_visit()
  def generic_visit( s, node ):
    node.Type = None

  def is_same( s, u, v ):
    """Return if the sub-AST at u and v are the same."""
    if type(u) is not type(v):
      return False
    return u == v

  def visit_Assign( s, node ):
    # RHS should have the same type as LHS
    rhs_type = node.value.Type.get_dtype()
    lhs_type = node.target.Type.get_dtype()
    if not lhs_type( rhs_type ):
      raise PyMTLTypeError( s.blk, node.ast,
        'Unagreeable types {} and {}!'.format( lhs_type, rhs_type ) )
    node.Type = None

  def visit_FreeVar( s, node ):
    if not node.name in s.freevars.keys():
      s.freevars[ node.name ] = node.obj
    t = rt.get_rtlir( node.obj )
    if isinstance( t, rt.Const ) and isinstance( t.get_dtype(), rdt.Vector ):
      node._value = mk_bits( t.get_dtype().get_length() )( node.obj )
    node.Type = t

  def visit_Base( s, node ):
    # Mark this node as having type rt.Component
    # In L1 the `s` top component is the only possible base
    node.Type = rt.get_rtlir( node.base )
    if not isinstance( node.Type, rt.Component ):
      raise PyMTLTypeError( s.blk, node.ast,
        '{} is not a rt.Component!'.format( node ) )

  def visit_Number( s, node ):
    # By default, number literals have bitwidth of 32
    node.Type = rt.get_rtlir( node.value )
    node._value = Bits32( node.value )

  def visit_Concat( s, node ):
    nbits = 0
    for child in node.values:
      if not isinstance(child.Type, rt.Signal):
        raise PyMTLTypeError( s.blk, node.ast,
          '{} is not a signal!'.format( child ) )
      nbits += child.Type.get_dtype().get_length()
    node.Type = rt.Wire( rdt.Vector( nbits ) )

  def visit_ZeroExt( s, node ):
    try:
      new_nbits = node.nbits._value
    except AttributeError:
      raise PyMTLTypeError( s.blk, node.ast,
        '{} is not a constant number!'.format( node.nbits ) )
    child_type = node.value.Type
    old_nbits = child_type.get_dtype().get_length()
    if new_nbits <= old_nbits:
      raise PyMTLTypeError( s.blk, node.ast,
        '{} is not greater than {}!'.format(new_nbits, old_nbits) )
    node.Type = copy.copy( child_type )
    node.Type.dtype = rdt.Vector( new_nbits )

  def visit_SignExt( s, node ):
    try:
      new_nbits = node.nbits._value
    except AttributeError:
      raise PyMTLTypeError( s.blk, node.ast,
        '{} is not a constant number!'.format( node.nbits ) )
    child_type = node.value.Type
    old_nbits = child_type.get_dtype().get_length()
    if new_nbits <= old_nbits:
      raise PyMTLTypeError( s.blk, node.ast,
        '{} is not greater than {}!'.format(new_nbits, old_nbits) )
    node.Type = copy.copy( child_type )
    node.Type.dtype = rdt.Vector( new_nbits )

  def visit_SizeCast( s, node ):
    nbits = node.nbits
    Type = node.value.Type

    # We do not check for bitwidth mismatch here because the user should
    # be able to explicitly convert signals/constatns to different bitwidth.
    node.Type = copy.copy( Type )
    node.Type.dtype = rdt.Vector( nbits )

    try:
      node._value = node.value._value
    except AttributeError:
      pass

  def visit_Attribute( s, node ):
    # Attribute supported at L1: CurCompAttr
    if isinstance( node.value, bir.Base ):
      if not node.value.Type.has_property( node.attr ):
        raise PyMTLTypeError( s.blk, node.ast,
          'type {} does not have attribute {}!'.format(node.value.Type, node.attr))

    else:
      raise PyMTLTypeError( s.blk, node.ast,
        'non-component attribute is not supported at L1!'.format(node.attr, node.value.Type))
    # value.attr has the type that is specified by the base
    node.Type = node.value.Type.get_property( node.attr )

  def visit_Index( s, node ):
    idx = getattr( node.idx, '_value', None )
    if isinstance( node.value.Type, rt.Array ):
      if idx is not None and not (0 <= idx < node.value.Type.get_dim_sizes()[0]):
        raise PyMTLTypeError( s.blk, node.ast, 'array index out of range!' )
      # Unpacked array index must be a static constant integer!
      subtype = node.value.Type.get_sub_type()
      if idx is not None and not isinstance( subtype, ( rt.Port, rt.Wire, rt.Const ) ):
        raise PyMTLTypeError( s.blk, node.ast,
'index of unpacked array {} must be a constant integer expression!'.format(node.value))
      node.Type = node.value.Type.get_next_dim_type()

    elif isinstance( node.value.Type, rt.Signal ):
      dtype = node.value.Type.get_dtype()
      if node.value.Type.is_packed_indexable():
        if idx is not None and not (0 <= idx < dtype.get_length()):
          raise PyMTLTypeError( s.blk, node.ast,
            'bit selection index out of range!' )
        node.Type = node.value.Type.get_next_dim_type()
      elif isinstance( dtype, rdt.Vector ):
        if idx is not None and not(0 <= idx < dtype.get_length()):
          raise PyMTLTypeError( s.blk, node.ast,
            'bit selection index out of range!' )
        node.Type = rt.Wire( rdt.Vector( 1 ) )
      else:
        raise PyMTLTypeError( s.blk, node.ast,
          'cannot perform index on {}!'.format(dtype))

    else:
      # Should be unreachable
      raise PyMTLTypeError( s.blk, node.ast,
        'cannot perform index on {}!'.format(node.value.Type))

  def visit_Slice( s, node ):
    lower_val = getattr( node.lower, '_value', None )
    upper_val  = getattr( node.upper, '_value', None )
    dtype = node.value.Type.get_dtype()

    if not isinstance( dtype, rdt.Vector ):
      raise PyMTLTypeError( s.blk, node.ast,
        'cannot perform slicing on type {}!'.format(dtype))

    if not lower_val is None and not upper_val is None:
      signal_nbits = dtype.get_length()
      # upper bound must be strictly larger than the lower bound
      if ( lower_val >= upper_val ):
        raise PyMTLTypeError( s.blk, node.ast,
          'the upper bound of a slice must be larger than the lower bound!' )
      # upper & lower bound should be less than the bit width of the signal
      if not ( 0 <= lower_val < upper_val <= signal_nbits ):
        raise PyMTLTypeError( s.blk, node.ast,
          'upper/lower bound of slice out of width of signal!' )
      node.Type = rt.Wire( rdt.Vector( int( upper_val - lower_val ) ) )

    else:
      # Try to special case the constant-stride part selection
      try:
        assert isinstance( node.upper, bir.BinOp )
        assert isinstance( node.upper.op, bir.Add )
        nbits = node.upper.right
        assert s.is_same( node.lower, node.upper.left )
        node.Type = rt.Wire( rdt.Vector( nbits ) )
      except Exception:
        raise PyMTLTypeError( s.blk, node.ast, 'slice bounds must be constant!' )