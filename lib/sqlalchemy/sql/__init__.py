# sql/__init__.py
# Copyright (C) 2005-2013 the SQLAlchemy authors and contributors <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: http://www.opensource.org/licenses/mit-license.php

from .expression import (
    Alias,
    ClauseElement,
    ColumnCollection,
    ColumnElement,
    CompoundSelect,
    Delete,
    FromClause,
    Insert,
    Join,
    Select,
    Selectable,
    TableClause,
    Update,
    alias,
    and_,
    asc,
    between,
    bindparam,
    case,
    cast,
    collate,
    column,
    delete,
    desc,
    distinct,
    except_,
    except_all,
    exists,
    extract,
    false,
    func,
    insert,
    intersect,
    intersect_all,
    join,
    label,
    literal,
    literal_column,
    modifier,
    not_,
    null,
    or_,
    outerjoin,
    outparam,
    over,
    select,
    subquery,
    table,
    text,
    true,
    tuple_,
    type_coerce,
    union,
    union_all,
    update,
    )

from .visitors import ClauseVisitor

__tmp = list(locals().keys())
__all__ = sorted([i for i in __tmp if not i.startswith('__')])


from . import schema, expression

from .annotation import _prepare_annotations
from .elements import ColumnElement, AnnotatedColumnElement
from .selectable import FromClause, AnnotatedFromClause
_prepare_annotations(ColumnElement, AnnotatedColumnElement)
_prepare_annotations(FromClause, AnnotatedFromClause)

from .. import util as _sa_util
_sa_util.importlater.resolve_all("sqlalchemy.sql")
import sqlalchemy