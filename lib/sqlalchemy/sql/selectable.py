from .elements import ClauseElement, TextClause
from .. import inspection
from .. import util
from .. import exc

def _interpret_as_from(element):
    insp = inspection.inspect(element, raiseerr=False)
    if insp is None:
        if isinstance(element, util.string_types):
            return TextClause(util.text_type(element))
    elif hasattr(insp, "selectable"):
        return insp.selectable
    raise exc.ArgumentError("FROM expression expected")

def _interpret_as_select(element):
    element = _interpret_as_from(element)
    if isinstance(element, Alias):
        element = element.original
    if not isinstance(element, Select):
        element = element.select()
    return element



class Selectable(ClauseElement):
    """mark a class as being selectable"""
    __visit_name__ = 'selectable'

    is_selectable = True

    @property
    def selectable(self):
        return self


class FromClause(Selectable):
    """Represent an element that can be used within the ``FROM``
    clause of a ``SELECT`` statement.

    The most common forms of :class:`.FromClause` are the
    :class:`.Table` and the :func:`.select` constructs.  Key
    features common to all :class:`.FromClause` objects include:

    * a :attr:`.c` collection, which provides per-name access to a collection
      of :class:`.ColumnElement` objects.
    * a :attr:`.primary_key` attribute, which is a collection of all those
      :class:`.ColumnElement` objects that indicate the ``primary_key`` flag.
    * Methods to generate various derivations of a "from" clause, including
      :meth:`.FromClause.alias`, :meth:`.FromClause.join`,
      :meth:`.FromClause.select`.


    """
    __visit_name__ = 'fromclause'
    named_with_column = False
    _hide_froms = []
    quote = None
    schema = None
    _memoized_property = util.group_expirable_memoized_property(["_columns"])

    def count(self, whereclause=None, **params):
        """return a SELECT COUNT generated against this
        :class:`.FromClause`."""

        if self.primary_key:
            col = list(self.primary_key)[0]
        else:
            col = list(self.columns)[0]
        return select(
                    [func.count(col).label('tbl_row_count')],
                    whereclause,
                    from_obj=[self],
                    **params)

    def select(self, whereclause=None, **params):
        """return a SELECT of this :class:`.FromClause`.

        .. seealso::

            :func:`~.sql.expression.select` - general purpose
            method which allows for arbitrary column lists.

        """

        return select([self], whereclause, **params)

    def join(self, right, onclause=None, isouter=False):
        """return a join of this :class:`.FromClause` against another
        :class:`.FromClause`."""

        return Join(self, right, onclause, isouter)

    def outerjoin(self, right, onclause=None):
        """return an outer join of this :class:`.FromClause` against another
        :class:`.FromClause`."""

        return Join(self, right, onclause, True)

    def alias(self, name=None, flat=False):
        """return an alias of this :class:`.FromClause`.

        This is shorthand for calling::

            from sqlalchemy import alias
            a = alias(self, name=name)

        See :func:`~.expression.alias` for details.

        """

        return Alias(self, name)

    def is_derived_from(self, fromclause):
        """Return True if this FromClause is 'derived' from the given
        FromClause.

        An example would be an Alias of a Table is derived from that Table.

        """
        # this is essentially an "identity" check in the base class.
        # Other constructs override this to traverse through
        # contained elements.
        return fromclause in self._cloned_set

    def _is_lexical_equivalent(self, other):
        """Return True if this FromClause and the other represent
        the same lexical identity.

        This tests if either one is a copy of the other, or
        if they are the same via annotation identity.

        """
        return self._cloned_set.intersection(other._cloned_set)

    def replace_selectable(self, old, alias):
        """replace all occurrences of FromClause 'old' with the given Alias
        object, returning a copy of this :class:`.FromClause`.

        """

        return sqlutil.ClauseAdapter(alias).traverse(self)

    def correspond_on_equivalents(self, column, equivalents):
        """Return corresponding_column for the given column, or if None
        search for a match in the given dictionary.

        """
        col = self.corresponding_column(column, require_embedded=True)
        if col is None and col in equivalents:
            for equiv in equivalents[col]:
                nc = self.corresponding_column(equiv, require_embedded=True)
                if nc:
                    return nc
        return col

    def corresponding_column(self, column, require_embedded=False):
        """Given a :class:`.ColumnElement`, return the exported
        :class:`.ColumnElement` object from this :class:`.Selectable`
        which corresponds to that original
        :class:`~sqlalchemy.schema.Column` via a common ancestor
        column.

        :param column: the target :class:`.ColumnElement` to be matched

        :param require_embedded: only return corresponding columns for
        the given :class:`.ColumnElement`, if the given
        :class:`.ColumnElement` is actually present within a sub-element
        of this :class:`.FromClause`.  Normally the column will match if
        it merely shares a common ancestor with one of the exported
        columns of this :class:`.FromClause`.

        """

        def embedded(expanded_proxy_set, target_set):
            for t in target_set.difference(expanded_proxy_set):
                if not set(_expand_cloned([t])
                            ).intersection(expanded_proxy_set):
                    return False
            return True

        # don't dig around if the column is locally present
        if self.c.contains_column(column):
            return column
        col, intersect = None, None
        target_set = column.proxy_set
        cols = self.c
        for c in cols:
            expanded_proxy_set = set(_expand_cloned(c.proxy_set))
            i = target_set.intersection(expanded_proxy_set)
            if i and (not require_embedded
                      or embedded(expanded_proxy_set, target_set)):
                if col is None:

                    # no corresponding column yet, pick this one.

                    col, intersect = c, i
                elif len(i) > len(intersect):

                    # 'c' has a larger field of correspondence than
                    # 'col'. i.e. selectable.c.a1_x->a1.c.x->table.c.x
                    # matches a1.c.x->table.c.x better than
                    # selectable.c.x->table.c.x does.

                    col, intersect = c, i
                elif i == intersect:

                    # they have the same field of correspondence. see
                    # which proxy_set has fewer columns in it, which
                    # indicates a closer relationship with the root
                    # column. Also take into account the "weight"
                    # attribute which CompoundSelect() uses to give
                    # higher precedence to columns based on vertical
                    # position in the compound statement, and discard
                    # columns that have no reference to the target
                    # column (also occurs with CompoundSelect)

                    col_distance = util.reduce(operator.add,
                            [sc._annotations.get('weight', 1) for sc in
                            col.proxy_set if sc.shares_lineage(column)])
                    c_distance = util.reduce(operator.add,
                            [sc._annotations.get('weight', 1) for sc in
                            c.proxy_set if sc.shares_lineage(column)])
                    if c_distance < col_distance:
                        col, intersect = c, i
        return col

    @property
    def description(self):
        """a brief description of this FromClause.

        Used primarily for error message formatting.

        """
        return getattr(self, 'name', self.__class__.__name__ + " object")

    def _reset_exported(self):
        """delete memoized collections when a FromClause is cloned."""

        self._memoized_property.expire_instance(self)

    @_memoized_property
    def columns(self):
        """A named-based collection of :class:`.ColumnElement` objects
        maintained by this :class:`.FromClause`.

        The :attr:`.columns`, or :attr:`.c` collection, is the gateway
        to the construction of SQL expressions using table-bound or
        other selectable-bound columns::

            select([mytable]).where(mytable.c.somecolumn == 5)

        """

        if '_columns' not in self.__dict__:
            self._init_collections()
            self._populate_column_collection()
        return self._columns.as_immutable()

    @_memoized_property
    def primary_key(self):
        """Return the collection of Column objects which comprise the
        primary key of this FromClause."""

        self._init_collections()
        self._populate_column_collection()
        return self.primary_key

    @_memoized_property
    def foreign_keys(self):
        """Return the collection of ForeignKey objects which this
        FromClause references."""

        self._init_collections()
        self._populate_column_collection()
        return self.foreign_keys

    c = property(attrgetter('columns'),
            doc="An alias for the :attr:`.columns` attribute.")
    _select_iterable = property(attrgetter('columns'))

    def _init_collections(self):
        assert '_columns' not in self.__dict__
        assert 'primary_key' not in self.__dict__
        assert 'foreign_keys' not in self.__dict__

        self._columns = ColumnCollection()
        self.primary_key = ColumnSet()
        self.foreign_keys = set()

    @property
    def _cols_populated(self):
        return '_columns' in self.__dict__

    def _populate_column_collection(self):
        """Called on subclasses to establish the .c collection.

        Each implementation has a different way of establishing
        this collection.

        """

    def _refresh_for_new_column(self, column):
        """Given a column added to the .c collection of an underlying
        selectable, produce the local version of that column, assuming this
        selectable ultimately should proxy this column.

        this is used to "ping" a derived selectable to add a new column
        to its .c. collection when a Column has been added to one of the
        Table objects it ultimtely derives from.

        If the given selectable hasn't populated it's .c. collection yet,
        it should at least pass on the message to the contained selectables,
        but it will return None.

        This method is currently used by Declarative to allow Table
        columns to be added to a partially constructed inheritance
        mapping that may have already produced joins.  The method
        isn't public right now, as the full span of implications
        and/or caveats aren't yet clear.

        It's also possible that this functionality could be invoked by
        default via an event, which would require that
        selectables maintain a weak referencing collection of all
        derivations.

        """
        if not self._cols_populated:
            return None
        elif column.key in self.columns and self.columns[column.key] is column:
            return column
        else:
            return None


class Join(FromClause):
    """represent a ``JOIN`` construct between two :class:`.FromClause`
    elements.

    The public constructor function for :class:`.Join` is the module-level
    :func:`join()` function, as well as the :func:`join()` method available
    off all :class:`.FromClause` subclasses.

    """
    __visit_name__ = 'join'

    def __init__(self, left, right, onclause=None, isouter=False):
        """Construct a new :class:`.Join`.

        The usual entrypoint here is the :func:`~.expression.join`
        function or the :meth:`.FromClause.join` method of any
        :class:`.FromClause` object.

        """
        self.left = _interpret_as_from(left)
        self.right = _interpret_as_from(right).self_group()

        if onclause is None:
            self.onclause = self._match_primaries(self.left, self.right)
        else:
            self.onclause = onclause

        self.isouter = isouter

    @property
    def description(self):
        return "Join object on %s(%d) and %s(%d)" % (
            self.left.description,
            id(self.left),
            self.right.description,
            id(self.right))

    def is_derived_from(self, fromclause):
        return fromclause is self or \
                self.left.is_derived_from(fromclause) or \
                self.right.is_derived_from(fromclause)

    def self_group(self, against=None):
        return FromGrouping(self)

    def _populate_column_collection(self):
        columns = [c for c in self.left.columns] + \
                        [c for c in self.right.columns]

        self.primary_key.extend(sqlutil.reduce_columns(
                (c for c in columns if c.primary_key), self.onclause))
        self._columns.update((col._label, col) for col in columns)
        self.foreign_keys.update(itertools.chain(
                        *[col.foreign_keys for col in columns]))

    def _refresh_for_new_column(self, column):
        col = self.left._refresh_for_new_column(column)
        if col is None:
            col = self.right._refresh_for_new_column(column)
        if col is not None:
            if self._cols_populated:
                self._columns[col._label] = col
                self.foreign_keys.add(col)
                if col.primary_key:
                    self.primary_key.add(col)
                return col
        return None

    def _copy_internals(self, clone=_clone, **kw):
        self._reset_exported()
        self.left = clone(self.left, **kw)
        self.right = clone(self.right, **kw)
        self.onclause = clone(self.onclause, **kw)

    def get_children(self, **kwargs):
        return self.left, self.right, self.onclause

    def _match_primaries(self, left, right):
        if isinstance(left, Join):
            left_right = left.right
        else:
            left_right = None
        return sqlutil.join_condition(left, right, a_subset=left_right)

    def select(self, whereclause=None, **kwargs):
        """Create a :class:`.Select` from this :class:`.Join`.

        The equivalent long-hand form, given a :class:`.Join` object
        ``j``, is::

            from sqlalchemy import select
            j = select([j.left, j.right], **kw).\\
                        where(whereclause).\\
                        select_from(j)

        :param whereclause: the WHERE criterion that will be sent to
          the :func:`select()` function

        :param \**kwargs: all other kwargs are sent to the
          underlying :func:`select()` function.

        """
        collist = [self.left, self.right]

        return select(collist, whereclause, from_obj=[self], **kwargs)

    @property
    def bind(self):
        return self.left.bind or self.right.bind

    def alias(self, name=None, flat=False):
        """return an alias of this :class:`.Join`.

        The default behavior here is to first produce a SELECT
        construct from this :class:`.Join`, then to produce a
        :class:`.Alias` from that.  So given a join of the form::

            j = table_a.join(table_b, table_a.c.id == table_b.c.a_id)

        The JOIN by itself would look like::

            table_a JOIN table_b ON table_a.id = table_b.a_id

        Whereas the alias of the above, ``j.alias()``, would in a
        SELECT context look like::

            (SELECT table_a.id AS table_a_id, table_b.id AS table_b_id,
                table_b.a_id AS table_b_a_id
                FROM table_a
                JOIN table_b ON table_a.id = table_b.a_id) AS anon_1

        The equivalent long-hand form, given a :class:`.Join` object
        ``j``, is::

            from sqlalchemy import select, alias
            j = alias(
                select([j.left, j.right]).\\
                    select_from(j).\\
                    with_labels(True).\\
                    correlate(False),
                name=name
            )

        The selectable produced by :meth:`.Join.alias` features the same
        columns as that of the two individual selectables presented under
        a single name - the individual columns are "auto-labeled", meaning
        the ``.c.`` collection of the resulting :class:`.Alias` represents
        the names of the individual columns using a ``<tablename>_<columname>``
        scheme::

            j.c.table_a_id
            j.c.table_b_a_id

        :meth:`.Join.alias` also features an alternate
        option for aliasing joins which produces no enclosing SELECT and
        does not normally apply labels to the column names.  The
        ``flat=True`` option will call :meth:`.FromClause.alias`
        against the left and right sides individually.
        Using this option, no new ``SELECT`` is produced;
        we instead, from a construct as below::

            j = table_a.join(table_b, table_a.c.id == table_b.c.a_id)
            j = j.alias(flat=True)

        we get a result like this::

            table_a AS table_a_1 JOIN table_b AS table_b_1 ON
            table_a_1.id = table_b_1.a_id

        The ``flat=True`` argument is also propagated to the contained
        selectables, so that a composite join such as::

            j = table_a.join(
                    table_b.join(table_c,
                            table_b.c.id == table_c.c.b_id),
                    table_b.c.a_id == table_a.c.id
                ).alias(flat=True)

        Will produce an expression like::

            table_a AS table_a_1 JOIN (
                    table_b AS table_b_1 JOIN table_c AS table_c_1
                    ON table_b_1.id = table_c_1.b_id
            ) ON table_a_1.id = table_b_1.a_id

        The standalone :func:`experssion.alias` function as well as the
        base :meth:`.FromClause.alias` method also support the ``flat=True``
        argument as a no-op, so that the argument can be passed to the
        ``alias()`` method of any selectable.

        .. versionadded:: 0.9.0 Added the ``flat=True`` option to create
          "aliases" of joins without enclosing inside of a SELECT
          subquery.

        :param name: name given to the alias.

        :param flat: if True, produce an alias of the left and right
         sides of this :class:`.Join` and return the join of those
         two selectables.   This produces join expression that does not
         include an enclosing SELECT.

         .. versionadded:: 0.9.0

        .. seealso::

            :func:`~.expression.alias`

        """
        if flat:
            assert name is None, "Can't send name argument with flat"
            left_a, right_a = self.left.alias(flat=True), \
                                self.right.alias(flat=True)
            adapter = sqlutil.ClauseAdapter(left_a).\
                        chain(sqlutil.ClauseAdapter(right_a))

            return left_a.join(right_a,
                        adapter.traverse(self.onclause), isouter=self.isouter)
        else:
            return self.select(use_labels=True, correlate=False).alias(name)

    @property
    def _hide_froms(self):
        return itertools.chain(*[_from_objects(x.left, x.right)
                               for x in self._cloned_set])

    @property
    def _from_objects(self):
        return [self] + \
                self.onclause._from_objects + \
                self.left._from_objects + \
                self.right._from_objects


class Alias(FromClause):
    """Represents an table or selectable alias (AS).

    Represents an alias, as typically applied to any table or
    sub-select within a SQL statement using the ``AS`` keyword (or
    without the keyword on certain databases such as Oracle).

    This object is constructed from the :func:`~.expression.alias` module level
    function as well as the :meth:`.FromClause.alias` method available on all
    :class:`.FromClause` subclasses.

    """

    __visit_name__ = 'alias'
    named_with_column = True

    def __init__(self, selectable, name=None):
        baseselectable = selectable
        while isinstance(baseselectable, Alias):
            baseselectable = baseselectable.element
        self.original = baseselectable
        self.supports_execution = baseselectable.supports_execution
        if self.supports_execution:
            self._execution_options = baseselectable._execution_options
        self.element = selectable
        if name is None:
            if self.original.named_with_column:
                name = getattr(self.original, 'name', None)
            name = _anonymous_label('%%(%d %s)s' % (id(self), name
                    or 'anon'))
        self.name = name

    @property
    def description(self):
        if util.py3k:
            return self.name
        else:
            return self.name.encode('ascii', 'backslashreplace')

    def as_scalar(self):
        try:
            return self.element.as_scalar()
        except AttributeError:
            raise AttributeError("Element %s does not support "
                                 "'as_scalar()'" % self.element)

    def is_derived_from(self, fromclause):
        if fromclause in self._cloned_set:
            return True
        return self.element.is_derived_from(fromclause)

    def _populate_column_collection(self):
        for col in self.element.columns:
            col._make_proxy(self)

    def _refresh_for_new_column(self, column):
        col = self.element._refresh_for_new_column(column)
        if col is not None:
            if not self._cols_populated:
                return None
            else:
                return col._make_proxy(self)
        else:
            return None

    def _copy_internals(self, clone=_clone, **kw):
        # don't apply anything to an aliased Table
        # for now.   May want to drive this from
        # the given **kw.
        if isinstance(self.element, TableClause):
            return
        self._reset_exported()
        self.element = clone(self.element, **kw)
        baseselectable = self.element
        while isinstance(baseselectable, Alias):
            baseselectable = baseselectable.element
        self.original = baseselectable

    def get_children(self, column_collections=True, **kw):
        if column_collections:
            for c in self.c:
                yield c
        yield self.element

    @property
    def _from_objects(self):
        return [self]

    @property
    def bind(self):
        return self.element.bind


class CTE(Alias):
    """Represent a Common Table Expression.

    The :class:`.CTE` object is obtained using the
    :meth:`.SelectBase.cte` method from any selectable.
    See that method for complete examples.

    .. versionadded:: 0.7.6

    """
    __visit_name__ = 'cte'

    def __init__(self, selectable,
                        name=None,
                        recursive=False,
                        _cte_alias=None,
                        _restates=frozenset()):
        self.recursive = recursive
        self._cte_alias = _cte_alias
        self._restates = _restates
        super(CTE, self).__init__(selectable, name=name)

    def alias(self, name=None, flat=False):
        return CTE(
            self.original,
            name=name,
            recursive=self.recursive,
            _cte_alias=self,
          )

    def union(self, other):
        return CTE(
            self.original.union(other),
            name=self.name,
            recursive=self.recursive,
            _restates=self._restates.union([self])
        )

    def union_all(self, other):
        return CTE(
            self.original.union_all(other),
            name=self.name,
            recursive=self.recursive,
            _restates=self._restates.union([self])
        )




class FromGrouping(FromClause):
    """Represent a grouping of a FROM clause"""
    __visit_name__ = 'grouping'

    def __init__(self, element):
        self.element = element

    def _init_collections(self):
        pass

    @property
    def columns(self):
        return self.element.columns

    @property
    def primary_key(self):
        return self.element.primary_key

    @property
    def foreign_keys(self):
        return self.element.foreign_keys

    def is_derived_from(self, element):
        return self.element.is_derived_from(element)

    def alias(self, **kw):
        return FromGrouping(self.element.alias(**kw))

    @property
    def _hide_froms(self):
        return self.element._hide_froms

    def get_children(self, **kwargs):
        return self.element,

    def _copy_internals(self, clone=_clone, **kw):
        self.element = clone(self.element, **kw)

    @property
    def _from_objects(self):
        return self.element._from_objects

    def __getattr__(self, attr):
        return getattr(self.element, attr)

    def __getstate__(self):
        return {'element': self.element}

    def __setstate__(self, state):
        self.element = state['element']

class TableClause(Immutable, FromClause):
    """Represents a minimal "table" construct.

    The constructor for :class:`.TableClause` is the
    :func:`~.expression.table` function.   This produces
    a lightweight table object that has only a name and a
    collection of columns, which are typically produced
    by the :func:`~.expression.column` function::

        from sqlalchemy.sql import table, column

        user = table("user",
                column("id"),
                column("name"),
                column("description"),
        )

    The :class:`.TableClause` construct serves as the base for
    the more commonly used :class:`~.schema.Table` object, providing
    the usual set of :class:`~.expression.FromClause` services including
    the ``.c.`` collection and statement generation methods.

    It does **not** provide all the additional schema-level services
    of :class:`~.schema.Table`, including constraints, references to other
    tables, or support for :class:`.MetaData`-level services.  It's useful
    on its own as an ad-hoc construct used to generate quick SQL
    statements when a more fully fledged :class:`~.schema.Table`
    is not on hand.

    """

    __visit_name__ = 'table'

    named_with_column = True

    implicit_returning = False
    """:class:`.TableClause` doesn't support having a primary key or column
    -level defaults, so implicit returning doesn't apply."""

    _autoincrement_column = None
    """No PK or default support so no autoincrement column."""

    def __init__(self, name, *columns):
        super(TableClause, self).__init__()
        self.name = self.fullname = name
        self._columns = ColumnCollection()
        self.primary_key = ColumnSet()
        self.foreign_keys = set()
        for c in columns:
            self.append_column(c)

    def _init_collections(self):
        pass

    @util.memoized_property
    def description(self):
        if util.py3k:
            return self.name
        else:
            return self.name.encode('ascii', 'backslashreplace')

    def append_column(self, c):
        self._columns[c.key] = c
        c.table = self

    def get_children(self, column_collections=True, **kwargs):
        if column_collections:
            return [c for c in self.c]
        else:
            return []

    def count(self, whereclause=None, **params):
        """return a SELECT COUNT generated against this
        :class:`.TableClause`."""

        if self.primary_key:
            col = list(self.primary_key)[0]
        else:
            col = list(self.columns)[0]
        return select(
                    [func.count(col).label('tbl_row_count')],
                    whereclause,
                    from_obj=[self],
                    **params)

    def insert(self, values=None, inline=False, **kwargs):
        """Generate an :func:`.insert` construct against this
        :class:`.TableClause`.

        E.g.::

            table.insert().values(name='foo')

        See :func:`.insert` for argument and usage information.

        """

        return insert(self, values=values, inline=inline, **kwargs)

    def update(self, whereclause=None, values=None, inline=False, **kwargs):
        """Generate an :func:`.update` construct against this
        :class:`.TableClause`.

        E.g.::

            table.update().where(table.c.id==7).values(name='foo')

        See :func:`.update` for argument and usage information.

        """

        return update(self, whereclause=whereclause,
                            values=values, inline=inline, **kwargs)

    def delete(self, whereclause=None, **kwargs):
        """Generate a :func:`.delete` construct against this
        :class:`.TableClause`.

        E.g.::

            table.delete().where(table.c.id==7)

        See :func:`.delete` for argument and usage information.

        """

        return delete(self, whereclause, **kwargs)

    @property
    def _from_objects(self):
        return [self]


class SelectBase(Executable, FromClause):
    """Base class for :class:`.Select` and ``CompoundSelects``."""

    _order_by_clause = ClauseList()
    _group_by_clause = ClauseList()
    _limit = None
    _offset = None

    def __init__(self,
            use_labels=False,
            for_update=False,
            limit=None,
            offset=None,
            order_by=None,
            group_by=None,
            bind=None,
            autocommit=None):
        self.use_labels = use_labels
        self.for_update = for_update
        if autocommit is not None:
            util.warn_deprecated('autocommit on select() is '
                                 'deprecated.  Use .execution_options(a'
                                 'utocommit=True)')
            self._execution_options = \
                self._execution_options.union(
                  {'autocommit': autocommit})
        if limit is not None:
            self._limit = util.asint(limit)
        if offset is not None:
            self._offset = util.asint(offset)
        self._bind = bind

        if order_by is not None:
            self._order_by_clause = ClauseList(*util.to_list(order_by))
        if group_by is not None:
            self._group_by_clause = ClauseList(*util.to_list(group_by))

    def as_scalar(self):
        """return a 'scalar' representation of this selectable, which can be
        used as a column expression.

        Typically, a select statement which has only one column in its columns
        clause is eligible to be used as a scalar expression.

        The returned object is an instance of
        :class:`ScalarSelect`.

        """
        return ScalarSelect(self)

    @_generative
    def apply_labels(self):
        """return a new selectable with the 'use_labels' flag set to True.

        This will result in column expressions being generated using labels
        against their table name, such as "SELECT somecolumn AS
        tablename_somecolumn". This allows selectables which contain multiple
        FROM clauses to produce a unique set of column names regardless of
        name conflicts among the individual FROM clauses.

        """
        self.use_labels = True

    def label(self, name):
        """return a 'scalar' representation of this selectable, embedded as a
        subquery with a label.

        .. seealso::

            :meth:`~.SelectBase.as_scalar`.

        """
        return self.as_scalar().label(name)

    def cte(self, name=None, recursive=False):
        """Return a new :class:`.CTE`, or Common Table Expression instance.

        Common table expressions are a SQL standard whereby SELECT
        statements can draw upon secondary statements specified along
        with the primary statement, using a clause called "WITH".
        Special semantics regarding UNION can also be employed to
        allow "recursive" queries, where a SELECT statement can draw
        upon the set of rows that have previously been selected.

        SQLAlchemy detects :class:`.CTE` objects, which are treated
        similarly to :class:`.Alias` objects, as special elements
        to be delivered to the FROM clause of the statement as well
        as to a WITH clause at the top of the statement.

        .. versionadded:: 0.7.6

        :param name: name given to the common table expression.  Like
         :meth:`._FromClause.alias`, the name can be left as ``None``
         in which case an anonymous symbol will be used at query
         compile time.
        :param recursive: if ``True``, will render ``WITH RECURSIVE``.
         A recursive common table expression is intended to be used in
         conjunction with UNION ALL in order to derive rows
         from those already selected.

        The following examples illustrate two examples from
        Postgresql's documentation at
        http://www.postgresql.org/docs/8.4/static/queries-with.html.

        Example 1, non recursive::

            from sqlalchemy import Table, Column, String, Integer, MetaData, \\
                select, func

            metadata = MetaData()

            orders = Table('orders', metadata,
                Column('region', String),
                Column('amount', Integer),
                Column('product', String),
                Column('quantity', Integer)
            )

            regional_sales = select([
                                orders.c.region,
                                func.sum(orders.c.amount).label('total_sales')
                            ]).group_by(orders.c.region).cte("regional_sales")


            top_regions = select([regional_sales.c.region]).\\
                    where(
                        regional_sales.c.total_sales >
                        select([
                            func.sum(regional_sales.c.total_sales)/10
                        ])
                    ).cte("top_regions")

            statement = select([
                        orders.c.region,
                        orders.c.product,
                        func.sum(orders.c.quantity).label("product_units"),
                        func.sum(orders.c.amount).label("product_sales")
                ]).where(orders.c.region.in_(
                    select([top_regions.c.region])
                )).group_by(orders.c.region, orders.c.product)

            result = conn.execute(statement).fetchall()

        Example 2, WITH RECURSIVE::

            from sqlalchemy import Table, Column, String, Integer, MetaData, \\
                select, func

            metadata = MetaData()

            parts = Table('parts', metadata,
                Column('part', String),
                Column('sub_part', String),
                Column('quantity', Integer),
            )

            included_parts = select([
                                parts.c.sub_part,
                                parts.c.part,
                                parts.c.quantity]).\\
                                where(parts.c.part=='our part').\\
                                cte(recursive=True)


            incl_alias = included_parts.alias()
            parts_alias = parts.alias()
            included_parts = included_parts.union_all(
                select([
                    parts_alias.c.part,
                    parts_alias.c.sub_part,
                    parts_alias.c.quantity
                ]).
                    where(parts_alias.c.part==incl_alias.c.sub_part)
            )

            statement = select([
                        included_parts.c.sub_part,
                        func.sum(included_parts.c.quantity).
                          label('total_quantity')
                    ]).\
                    select_from(included_parts.join(parts,
                                included_parts.c.part==parts.c.part)).\\
                    group_by(included_parts.c.sub_part)

            result = conn.execute(statement).fetchall()


        .. seealso::

            :meth:`.orm.query.Query.cte` - ORM version of :meth:`.SelectBase.cte`.

        """
        return CTE(self, name=name, recursive=recursive)

    @_generative
    @util.deprecated('0.6',
                     message=":func:`.autocommit` is deprecated. Use "
                     ":func:`.Executable.execution_options` with the "
                     "'autocommit' flag.")
    def autocommit(self):
        """return a new selectable with the 'autocommit' flag set to
        True."""

        self._execution_options = \
            self._execution_options.union({'autocommit': True})

    def _generate(self):
        """Override the default _generate() method to also clear out
        exported collections."""

        s = self.__class__.__new__(self.__class__)
        s.__dict__ = self.__dict__.copy()
        s._reset_exported()
        return s

    @_generative
    def limit(self, limit):
        """return a new selectable with the given LIMIT criterion
        applied."""

        self._limit = util.asint(limit)

    @_generative
    def offset(self, offset):
        """return a new selectable with the given OFFSET criterion
        applied."""

        self._offset = util.asint(offset)

    @_generative
    def order_by(self, *clauses):
        """return a new selectable with the given list of ORDER BY
        criterion applied.

        The criterion will be appended to any pre-existing ORDER BY
        criterion.

        """

        self.append_order_by(*clauses)

    @_generative
    def group_by(self, *clauses):
        """return a new selectable with the given list of GROUP BY
        criterion applied.

        The criterion will be appended to any pre-existing GROUP BY
        criterion.

        """

        self.append_group_by(*clauses)

    def append_order_by(self, *clauses):
        """Append the given ORDER BY criterion applied to this selectable.

        The criterion will be appended to any pre-existing ORDER BY criterion.

        This is an **in-place** mutation method; the
        :meth:`~.SelectBase.order_by` method is preferred, as it provides standard
        :term:`method chaining`.

        """
        if len(clauses) == 1 and clauses[0] is None:
            self._order_by_clause = ClauseList()
        else:
            if getattr(self, '_order_by_clause', None) is not None:
                clauses = list(self._order_by_clause) + list(clauses)
            self._order_by_clause = ClauseList(*clauses)

    def append_group_by(self, *clauses):
        """Append the given GROUP BY criterion applied to this selectable.

        The criterion will be appended to any pre-existing GROUP BY criterion.

        This is an **in-place** mutation method; the
        :meth:`~.SelectBase.group_by` method is preferred, as it provides standard
        :term:`method chaining`.

        """
        if len(clauses) == 1 and clauses[0] is None:
            self._group_by_clause = ClauseList()
        else:
            if getattr(self, '_group_by_clause', None) is not None:
                clauses = list(self._group_by_clause) + list(clauses)
            self._group_by_clause = ClauseList(*clauses)

    @property
    def _from_objects(self):
        return [self]


class CompoundSelect(SelectBase):
    """Forms the basis of ``UNION``, ``UNION ALL``, and other
        SELECT-based set operations."""

    __visit_name__ = 'compound_select'

    UNION = util.symbol('UNION')
    UNION_ALL = util.symbol('UNION ALL')
    EXCEPT = util.symbol('EXCEPT')
    EXCEPT_ALL = util.symbol('EXCEPT ALL')
    INTERSECT = util.symbol('INTERSECT')
    INTERSECT_ALL = util.symbol('INTERSECT ALL')

    def __init__(self, keyword, *selects, **kwargs):
        self._auto_correlate = kwargs.pop('correlate', False)
        self.keyword = keyword
        self.selects = []

        numcols = None

        # some DBs do not like ORDER BY in the inner queries of a UNION, etc.
        for n, s in enumerate(selects):
            s = _clause_element_as_expr(s)

            if not numcols:
                numcols = len(s.c)
            elif len(s.c) != numcols:
                raise exc.ArgumentError('All selectables passed to '
                        'CompoundSelect must have identical numbers of '
                        'columns; select #%d has %d columns, select '
                        '#%d has %d' % (1, len(self.selects[0].c), n
                        + 1, len(s.c)))

            self.selects.append(s.self_group(self))

        SelectBase.__init__(self, **kwargs)

    def _scalar_type(self):
        return self.selects[0]._scalar_type()

    def self_group(self, against=None):
        return FromGrouping(self)

    def is_derived_from(self, fromclause):
        for s in self.selects:
            if s.is_derived_from(fromclause):
                return True
        return False

    def _populate_column_collection(self):
        for cols in zip(*[s.c for s in self.selects]):

            # this is a slightly hacky thing - the union exports a
            # column that resembles just that of the *first* selectable.
            # to get at a "composite" column, particularly foreign keys,
            # you have to dig through the proxies collection which we
            # generate below.  We may want to improve upon this, such as
            # perhaps _make_proxy can accept a list of other columns
            # that are "shared" - schema.column can then copy all the
            # ForeignKeys in. this would allow the union() to have all
            # those fks too.

            proxy = cols[0]._make_proxy(self,
                    name=cols[0]._label if self.use_labels else None,
                    key=cols[0]._key_label if self.use_labels else None)

            # hand-construct the "_proxies" collection to include all
            # derived columns place a 'weight' annotation corresponding
            # to how low in the list of select()s the column occurs, so
            # that the corresponding_column() operation can resolve
            # conflicts

            proxy._proxies = [c._annotate({'weight': i + 1}) for (i,
                             c) in enumerate(cols)]

    def _refresh_for_new_column(self, column):
        for s in self.selects:
            s._refresh_for_new_column(column)

        if not self._cols_populated:
            return None

        raise NotImplementedError("CompoundSelect constructs don't support "
                "addition of columns to underlying selectables")

    def _copy_internals(self, clone=_clone, **kw):
        self._reset_exported()
        self.selects = [clone(s, **kw) for s in self.selects]
        if hasattr(self, '_col_map'):
            del self._col_map
        for attr in ('_order_by_clause', '_group_by_clause'):
            if getattr(self, attr) is not None:
                setattr(self, attr, clone(getattr(self, attr), **kw))

    def get_children(self, column_collections=True, **kwargs):
        return (column_collections and list(self.c) or []) \
            + [self._order_by_clause, self._group_by_clause] \
            + list(self.selects)

    def bind(self):
        if self._bind:
            return self._bind
        for s in self.selects:
            e = s.bind
            if e:
                return e
        else:
            return None

    def _set_bind(self, bind):
        self._bind = bind
    bind = property(bind, _set_bind)


class Select(HasPrefixes, SelectBase):
    """Represents a ``SELECT`` statement.

    .. seealso::

        :func:`~.expression.select` - the function which creates
        a :class:`.Select` object.

        :ref:`coretutorial_selecting` - Core Tutorial description
        of :func:`.select`.

    """

    __visit_name__ = 'select'

    _prefixes = ()
    _hints = util.immutabledict()
    _distinct = False
    _from_cloned = None
    _correlate = ()
    _correlate_except = None
    _memoized_property = SelectBase._memoized_property

    def __init__(self,
                columns,
                whereclause=None,
                from_obj=None,
                distinct=False,
                having=None,
                correlate=True,
                prefixes=None,
                **kwargs):
        """Construct a Select object.

        The public constructor for Select is the
        :func:`select` function; see that function for
        argument descriptions.

        Additional generative and mutator methods are available on the
        :class:`SelectBase` superclass.

        """
        self._auto_correlate = correlate
        if distinct is not False:
            if distinct is True:
                self._distinct = True
            else:
                self._distinct = [
                                _literal_as_text(e)
                                for e in util.to_list(distinct)
                            ]

        if from_obj is not None:
            self._from_obj = util.OrderedSet(
                                _interpret_as_from(f)
                                for f in util.to_list(from_obj))
        else:
            self._from_obj = util.OrderedSet()

        try:
            cols_present = bool(columns)
        except TypeError:
            raise exc.ArgumentError("columns argument to select() must "
                                "be a Python list or other iterable")

        if cols_present:
            self._raw_columns = []
            for c in columns:
                c = _interpret_as_column_or_from(c)
                if isinstance(c, ScalarSelect):
                    c = c.self_group(against=operators.comma_op)
                self._raw_columns.append(c)
        else:
            self._raw_columns = []

        if whereclause is not None:
            self._whereclause = _literal_as_text(whereclause)
        else:
            self._whereclause = None

        if having is not None:
            self._having = _literal_as_text(having)
        else:
            self._having = None

        if prefixes:
            self._setup_prefixes(prefixes)

        SelectBase.__init__(self, **kwargs)

    @property
    def _froms(self):
        # would love to cache this,
        # but there's just enough edge cases, particularly now that
        # declarative encourages construction of SQL expressions
        # without tables present, to just regen this each time.
        froms = []
        seen = set()
        translate = self._from_cloned

        def add(items):
            for item in items:
                if translate and item in translate:
                    item = translate[item]
                if not seen.intersection(item._cloned_set):
                    froms.append(item)
                seen.update(item._cloned_set)

        add(_from_objects(*self._raw_columns))
        if self._whereclause is not None:
            add(_from_objects(self._whereclause))
        add(self._from_obj)

        return froms

    def _get_display_froms(self, explicit_correlate_froms=None,
                                    implicit_correlate_froms=None):
        """Return the full list of 'from' clauses to be displayed.

        Takes into account a set of existing froms which may be
        rendered in the FROM clause of enclosing selects; this Select
        may want to leave those absent if it is automatically
        correlating.

        """
        froms = self._froms

        toremove = set(itertools.chain(*[
                            _expand_cloned(f._hide_froms)
                            for f in froms]))
        if toremove:
            # if we're maintaining clones of froms,
            # add the copies out to the toremove list.  only include
            # clones that are lexical equivalents.
            if self._from_cloned:
                toremove.update(
                    self._from_cloned[f] for f in
                    toremove.intersection(self._from_cloned)
                    if self._from_cloned[f]._is_lexical_equivalent(f)
                )
            # filter out to FROM clauses not in the list,
            # using a list to maintain ordering
            froms = [f for f in froms if f not in toremove]

        if self._correlate:
            to_correlate = self._correlate
            if to_correlate:
                froms = [
                    f for f in froms if f not in
                    _cloned_intersection(
                        _cloned_intersection(froms, explicit_correlate_froms or ()),
                        to_correlate
                    )
                ]

        if self._correlate_except is not None:

            froms = [
                f for f in froms if f not in
                _cloned_difference(
                    _cloned_intersection(froms, explicit_correlate_froms or ()),
                    self._correlate_except
                )
            ]

        if self._auto_correlate and \
            implicit_correlate_froms and \
            len(froms) > 1:

            froms = [
                f for f in froms if f not in
                _cloned_intersection(froms, implicit_correlate_froms)
            ]

            if not len(froms):
                raise exc.InvalidRequestError("Select statement '%s"
                        "' returned no FROM clauses due to "
                        "auto-correlation; specify "
                        "correlate(<tables>) to control "
                        "correlation manually." % self)

        return froms

    def _scalar_type(self):
        elem = self._raw_columns[0]
        cols = list(elem._select_iterable)
        return cols[0].type

    @property
    def froms(self):
        """Return the displayed list of FromClause elements."""

        return self._get_display_froms()

    @_generative
    def with_hint(self, selectable, text, dialect_name='*'):
        """Add an indexing hint for the given selectable to this
        :class:`.Select`.

        The text of the hint is rendered in the appropriate
        location for the database backend in use, relative
        to the given :class:`.Table` or :class:`.Alias` passed as the
        ``selectable`` argument. The dialect implementation
        typically uses Python string substitution syntax
        with the token ``%(name)s`` to render the name of
        the table or alias. E.g. when using Oracle, the
        following::

            select([mytable]).\\
                with_hint(mytable, "+ index(%(name)s ix_mytable)")

        Would render SQL as::

            select /*+ index(mytable ix_mytable) */ ... from mytable

        The ``dialect_name`` option will limit the rendering of a particular
        hint to a particular backend. Such as, to add hints for both Oracle
        and Sybase simultaneously::

            select([mytable]).\\
                with_hint(mytable, "+ index(%(name)s ix_mytable)", 'oracle').\\
                with_hint(mytable, "WITH INDEX ix_mytable", 'sybase')

        """
        self._hints = self._hints.union(
                    {(selectable, dialect_name): text})

    @property
    def type(self):
        raise exc.InvalidRequestError("Select objects don't have a type.  "
                    "Call as_scalar() on this Select object "
                    "to return a 'scalar' version of this Select.")

    @_memoized_property.method
    def locate_all_froms(self):
        """return a Set of all FromClause elements referenced by this Select.

        This set is a superset of that returned by the ``froms`` property,
        which is specifically for those FromClause elements that would
        actually be rendered.

        """
        froms = self._froms
        return froms + list(_from_objects(*froms))

    @property
    def inner_columns(self):
        """an iterator of all ColumnElement expressions which would
        be rendered into the columns clause of the resulting SELECT statement.

        """
        return _select_iterables(self._raw_columns)

    def is_derived_from(self, fromclause):
        if self in fromclause._cloned_set:
            return True

        for f in self.locate_all_froms():
            if f.is_derived_from(fromclause):
                return True
        return False

    def _copy_internals(self, clone=_clone, **kw):

        # Select() object has been cloned and probably adapted by the
        # given clone function.  Apply the cloning function to internal
        # objects

        # 1. keep a dictionary of the froms we've cloned, and what
        # they've become.  This is consulted later when we derive
        # additional froms from "whereclause" and the columns clause,
        # which may still reference the uncloned parent table.
        # as of 0.7.4 we also put the current version of _froms, which
        # gets cleared on each generation.  previously we were "baking"
        # _froms into self._from_obj.
        self._from_cloned = from_cloned = dict((f, clone(f, **kw))
                for f in self._from_obj.union(self._froms))

        # 3. update persistent _from_obj with the cloned versions.
        self._from_obj = util.OrderedSet(from_cloned[f] for f in
                self._from_obj)

        # the _correlate collection is done separately, what can happen
        # here is the same item is _correlate as in _from_obj but the
        # _correlate version has an annotation on it - (specifically
        # RelationshipProperty.Comparator._criterion_exists() does
        # this). Also keep _correlate liberally open with it's previous
        # contents, as this set is used for matching, not rendering.
        self._correlate = set(clone(f) for f in
                              self._correlate).union(self._correlate)

        # 4. clone other things.   The difficulty here is that Column
        # objects are not actually cloned, and refer to their original
        # .table, resulting in the wrong "from" parent after a clone
        # operation.  Hence _from_cloned and _from_obj supercede what is
        # present here.
        self._raw_columns = [clone(c, **kw) for c in self._raw_columns]
        for attr in '_whereclause', '_having', '_order_by_clause', \
            '_group_by_clause':
            if getattr(self, attr) is not None:
                setattr(self, attr, clone(getattr(self, attr), **kw))

        # erase exported column list, _froms collection,
        # etc.
        self._reset_exported()

    def get_children(self, column_collections=True, **kwargs):
        """return child elements as per the ClauseElement specification."""

        return (column_collections and list(self.columns) or []) + \
            self._raw_columns + list(self._froms) + \
            [x for x in
                (self._whereclause, self._having,
                    self._order_by_clause, self._group_by_clause)
            if x is not None]

    @_generative
    def column(self, column):
        """return a new select() construct with the given column expression
            added to its columns clause.

        """
        self.append_column(column)

    def reduce_columns(self, only_synonyms=True):
        """Return a new :func`.select` construct with redundantly
        named, equivalently-valued columns removed from the columns clause.

        "Redundant" here means two columns where one refers to the
        other either based on foreign key, or via a simple equality
        comparison in the WHERE clause of the statement.   The primary purpose
        of this method is to automatically construct a select statement
        with all uniquely-named columns, without the need to use
        table-qualified labels as :meth:`.apply_labels` does.

        When columns are omitted based on foreign key, the referred-to
        column is the one that's kept.  When columns are omitted based on
        WHERE eqivalence, the first column in the columns clause is the
        one that's kept.

        :param only_synonyms: when True, limit the removal of columns
         to those which have the same name as the equivalent.   Otherwise,
         all columns that are equivalent to another are removed.

        .. versionadded:: 0.8

        """
        return self.with_only_columns(
                sqlutil.reduce_columns(
                        self.inner_columns,
                        only_synonyms=only_synonyms,
                        *(self._whereclause, ) + tuple(self._from_obj)
                )
            )

    @_generative
    def with_only_columns(self, columns):
        """Return a new :func:`.select` construct with its columns
        clause replaced with the given columns.

        .. versionchanged:: 0.7.3
            Due to a bug fix, this method has a slight
            behavioral change as of version 0.7.3.
            Prior to version 0.7.3, the FROM clause of
            a :func:`.select` was calculated upfront and as new columns
            were added; in 0.7.3 and later it's calculated
            at compile time, fixing an issue regarding late binding
            of columns to parent tables.  This changes the behavior of
            :meth:`.Select.with_only_columns` in that FROM clauses no
            longer represented in the new list are dropped,
            but this behavior is more consistent in
            that the FROM clauses are consistently derived from the
            current columns clause.  The original intent of this method
            is to allow trimming of the existing columns list to be fewer
            columns than originally present; the use case of replacing
            the columns list with an entirely different one hadn't
            been anticipated until 0.7.3 was released; the usage
            guidelines below illustrate how this should be done.

        This method is exactly equivalent to as if the original
        :func:`.select` had been called with the given columns
        clause.   I.e. a statement::

            s = select([table1.c.a, table1.c.b])
            s = s.with_only_columns([table1.c.b])

        should be exactly equivalent to::

            s = select([table1.c.b])

        This means that FROM clauses which are only derived
        from the column list will be discarded if the new column
        list no longer contains that FROM::

            >>> table1 = table('t1', column('a'), column('b'))
            >>> table2 = table('t2', column('a'), column('b'))
            >>> s1 = select([table1.c.a, table2.c.b])
            >>> print s1
            SELECT t1.a, t2.b FROM t1, t2
            >>> s2 = s1.with_only_columns([table2.c.b])
            >>> print s2
            SELECT t2.b FROM t1

        The preferred way to maintain a specific FROM clause
        in the construct, assuming it won't be represented anywhere
        else (i.e. not in the WHERE clause, etc.) is to set it using
        :meth:`.Select.select_from`::

            >>> s1 = select([table1.c.a, table2.c.b]).\\
            ...         select_from(table1.join(table2,
            ...                 table1.c.a==table2.c.a))
            >>> s2 = s1.with_only_columns([table2.c.b])
            >>> print s2
            SELECT t2.b FROM t1 JOIN t2 ON t1.a=t2.a

        Care should also be taken to use the correct
        set of column objects passed to :meth:`.Select.with_only_columns`.
        Since the method is essentially equivalent to calling the
        :func:`.select` construct in the first place with the given
        columns, the columns passed to :meth:`.Select.with_only_columns`
        should usually be a subset of those which were passed
        to the :func:`.select` construct, not those which are available
        from the ``.c`` collection of that :func:`.select`.  That
        is::

            s = select([table1.c.a, table1.c.b]).select_from(table1)
            s = s.with_only_columns([table1.c.b])

        and **not**::

            # usually incorrect
            s = s.with_only_columns([s.c.b])

        The latter would produce the SQL::

            SELECT b
            FROM (SELECT t1.a AS a, t1.b AS b
            FROM t1), t1

        Since the :func:`.select` construct is essentially being
        asked to select both from ``table1`` as well as itself.

        """
        self._reset_exported()
        rc = []
        for c in columns:
            c = _interpret_as_column_or_from(c)
            if isinstance(c, ScalarSelect):
                c = c.self_group(against=operators.comma_op)
            rc.append(c)
        self._raw_columns = rc

    @_generative
    def where(self, whereclause):
        """return a new select() construct with the given expression added to
        its WHERE clause, joined to the existing clause via AND, if any.

        """

        self.append_whereclause(whereclause)

    @_generative
    def having(self, having):
        """return a new select() construct with the given expression added to
        its HAVING clause, joined to the existing clause via AND, if any.

        """
        self.append_having(having)

    @_generative
    def distinct(self, *expr):
        """Return a new select() construct which will apply DISTINCT to its
        columns clause.

        :param \*expr: optional column expressions.  When present,
         the Postgresql dialect will render a ``DISTINCT ON (<expressions>>)``
         construct.

        """
        if expr:
            expr = [_literal_as_text(e) for e in expr]
            if isinstance(self._distinct, list):
                self._distinct = self._distinct + expr
            else:
                self._distinct = expr
        else:
            self._distinct = True

    @_generative
    def select_from(self, fromclause):
        """return a new :func:`.select` construct with the
        given FROM expression
        merged into its list of FROM objects.

        E.g.::

            table1 = table('t1', column('a'))
            table2 = table('t2', column('b'))
            s = select([table1.c.a]).\\
                select_from(
                    table1.join(table2, table1.c.a==table2.c.b)
                )

        The "from" list is a unique set on the identity of each element,
        so adding an already present :class:`.Table` or other selectable
        will have no effect.   Passing a :class:`.Join` that refers
        to an already present :class:`.Table` or other selectable will have
        the effect of concealing the presence of that selectable as
        an individual element in the rendered FROM list, instead
        rendering it into a JOIN clause.

        While the typical purpose of :meth:`.Select.select_from` is to
        replace the default, derived FROM clause with a join, it can
        also be called with individual table elements, multiple times
        if desired, in the case that the FROM clause cannot be fully
        derived from the columns clause::

            select([func.count('*')]).select_from(table1)

        """
        self.append_from(fromclause)

    @_generative
    def correlate(self, *fromclauses):
        """return a new :class:`.Select` which will correlate the given FROM
        clauses to that of an enclosing :class:`.Select`.

        Calling this method turns off the :class:`.Select` object's
        default behavior of "auto-correlation".  Normally, FROM elements
        which appear in a :class:`.Select` that encloses this one via
        its :term:`WHERE clause`, ORDER BY, HAVING or
        :term:`columns clause` will be omitted from this :class:`.Select`
        object's :term:`FROM clause`.
        Setting an explicit correlation collection using the
        :meth:`.Select.correlate` method provides a fixed list of FROM objects
        that can potentially take place in this process.

        When :meth:`.Select.correlate` is used to apply specific FROM clauses
        for correlation, the FROM elements become candidates for
        correlation regardless of how deeply nested this :class:`.Select`
        object is, relative to an enclosing :class:`.Select` which refers to
        the same FROM object.  This is in contrast to the behavior of
        "auto-correlation" which only correlates to an immediate enclosing
        :class:`.Select`.   Multi-level correlation ensures that the link
        between enclosed and enclosing :class:`.Select` is always via
        at least one WHERE/ORDER BY/HAVING/columns clause in order for
        correlation to take place.

        If ``None`` is passed, the :class:`.Select` object will correlate
        none of its FROM entries, and all will render unconditionally
        in the local FROM clause.

        :param \*fromclauses: a list of one or more :class:`.FromClause`
         constructs, or other compatible constructs (i.e. ORM-mapped
         classes) to become part of the correlate collection.

         .. versionchanged:: 0.8.0 ORM-mapped classes are accepted by
            :meth:`.Select.correlate`.

        .. versionchanged:: 0.8.0 The :meth:`.Select.correlate` method no
           longer unconditionally removes entries from the FROM clause; instead,
           the candidate FROM entries must also be matched by a FROM entry
           located in an enclosing :class:`.Select`, which ultimately encloses
           this one as present in the WHERE clause, ORDER BY clause, HAVING
           clause, or columns clause of an enclosing :meth:`.Select`.

        .. versionchanged:: 0.8.2 explicit correlation takes place
           via any level of nesting of :class:`.Select` objects; in previous
           0.8 versions, correlation would only occur relative to the immediate
           enclosing :class:`.Select` construct.

        .. seealso::

            :meth:`.Select.correlate_except`

            :ref:`correlated_subqueries`

        """
        self._auto_correlate = False
        if fromclauses and fromclauses[0] is None:
            self._correlate = ()
        else:
            self._correlate = set(self._correlate).union(
                    _interpret_as_from(f) for f in fromclauses)

    @_generative
    def correlate_except(self, *fromclauses):
        """return a new :class:`.Select` which will omit the given FROM
        clauses from the auto-correlation process.

        Calling :meth:`.Select.correlate_except` turns off the
        :class:`.Select` object's default behavior of
        "auto-correlation" for the given FROM elements.  An element
        specified here will unconditionally appear in the FROM list, while
        all other FROM elements remain subject to normal auto-correlation
        behaviors.

        .. versionchanged:: 0.8.2 The :meth:`.Select.correlate_except`
           method was improved to fully prevent FROM clauses specified here
           from being omitted from the immediate FROM clause of this
           :class:`.Select`.

        If ``None`` is passed, the :class:`.Select` object will correlate
        all of its FROM entries.

        .. versionchanged:: 0.8.2 calling ``correlate_except(None)`` will
           correctly auto-correlate all FROM clauses.

        :param \*fromclauses: a list of one or more :class:`.FromClause`
         constructs, or other compatible constructs (i.e. ORM-mapped
         classes) to become part of the correlate-exception collection.

        .. seealso::

            :meth:`.Select.correlate`

            :ref:`correlated_subqueries`

        """

        self._auto_correlate = False
        if fromclauses and fromclauses[0] is None:
            self._correlate_except = ()
        else:
            self._correlate_except = set(self._correlate_except or ()).union(
                    _interpret_as_from(f) for f in fromclauses)

    def append_correlation(self, fromclause):
        """append the given correlation expression to this select()
        construct.

        This is an **in-place** mutation method; the
        :meth:`~.Select.correlate` method is preferred, as it provides standard
        :term:`method chaining`.

        """

        self._auto_correlate = False
        self._correlate = set(self._correlate).union(
                _interpret_as_from(f) for f in fromclause)

    def append_column(self, column):
        """append the given column expression to the columns clause of this
        select() construct.

        This is an **in-place** mutation method; the
        :meth:`~.Select.column` method is preferred, as it provides standard
        :term:`method chaining`.

        """
        self._reset_exported()
        column = _interpret_as_column_or_from(column)

        if isinstance(column, ScalarSelect):
            column = column.self_group(against=operators.comma_op)

        self._raw_columns = self._raw_columns + [column]

    def append_prefix(self, clause):
        """append the given columns clause prefix expression to this select()
        construct.

        This is an **in-place** mutation method; the
        :meth:`~.Select.prefix_with` method is preferred, as it provides standard
        :term:`method chaining`.

        """
        clause = _literal_as_text(clause)
        self._prefixes = self._prefixes + (clause,)

    def append_whereclause(self, whereclause):
        """append the given expression to this select() construct's WHERE
        criterion.

        The expression will be joined to existing WHERE criterion via AND.

        This is an **in-place** mutation method; the
        :meth:`~.Select.where` method is preferred, as it provides standard
        :term:`method chaining`.

        """
        self._reset_exported()
        whereclause = _literal_as_text(whereclause)

        if self._whereclause is not None:
            self._whereclause = and_(self._whereclause, whereclause)
        else:
            self._whereclause = whereclause

    def append_having(self, having):
        """append the given expression to this select() construct's HAVING
        criterion.

        The expression will be joined to existing HAVING criterion via AND.

        This is an **in-place** mutation method; the
        :meth:`~.Select.having` method is preferred, as it provides standard
        :term:`method chaining`.

        """
        if self._having is not None:
            self._having = and_(self._having, _literal_as_text(having))
        else:
            self._having = _literal_as_text(having)

    def append_from(self, fromclause):
        """append the given FromClause expression to this select() construct's
        FROM clause.

        This is an **in-place** mutation method; the
        :meth:`~.Select.select_from` method is preferred, as it provides standard
        :term:`method chaining`.

        """
        self._reset_exported()
        fromclause = _interpret_as_from(fromclause)
        self._from_obj = self._from_obj.union([fromclause])


    @_memoized_property
    def _columns_plus_names(self):
        if self.use_labels:
            names = set()
            def name_for_col(c):
                if c._label is None:
                    return (None, c)
                name = c._label
                if name in names:
                    name = c.anon_label
                else:
                    names.add(name)
                return name, c

            return [
                name_for_col(c)
                for c in util.unique_list(_select_iterables(self._raw_columns))
            ]
        else:
            return [
                (None, c)
                for c in util.unique_list(_select_iterables(self._raw_columns))
            ]

    def _populate_column_collection(self):
        for name, c in self._columns_plus_names:
            if not hasattr(c, '_make_proxy'):
                continue
            if name is None:
                key = None
            elif self.use_labels:
                key = c._key_label
                if key is not None and key in self.c:
                    key = c.anon_label
            else:
                key = None

            c._make_proxy(self, key=key,
                    name=name,
                    name_is_truncatable=True)

    def _refresh_for_new_column(self, column):
        for fromclause in self._froms:
            col = fromclause._refresh_for_new_column(column)
            if col is not None:
                if col in self.inner_columns and self._cols_populated:
                    our_label = col._key_label if self.use_labels else col.key
                    if our_label not in self.c:
                        return col._make_proxy(self,
                            name=col._label if self.use_labels else None,
                            key=col._key_label if self.use_labels else None,
                            name_is_truncatable=True)
                return None
        return None

    def self_group(self, against=None):
        """return a 'grouping' construct as per the ClauseElement
        specification.

        This produces an element that can be embedded in an expression. Note
        that this method is called automatically as needed when constructing
        expressions and should not require explicit use.

        """
        if isinstance(against, CompoundSelect):
            return self
        return FromGrouping(self)

    def union(self, other, **kwargs):
        """return a SQL UNION of this select() construct against the given
        selectable."""

        return union(self, other, **kwargs)

    def union_all(self, other, **kwargs):
        """return a SQL UNION ALL of this select() construct against the given
        selectable.

        """
        return union_all(self, other, **kwargs)

    def except_(self, other, **kwargs):
        """return a SQL EXCEPT of this select() construct against the given
        selectable."""

        return except_(self, other, **kwargs)

    def except_all(self, other, **kwargs):
        """return a SQL EXCEPT ALL of this select() construct against the
        given selectable.

        """
        return except_all(self, other, **kwargs)

    def intersect(self, other, **kwargs):
        """return a SQL INTERSECT of this select() construct against the given
        selectable.

        """
        return intersect(self, other, **kwargs)

    def intersect_all(self, other, **kwargs):
        """return a SQL INTERSECT ALL of this select() construct against the
        given selectable.

        """
        return intersect_all(self, other, **kwargs)

    def bind(self):
        if self._bind:
            return self._bind
        froms = self._froms
        if not froms:
            for c in self._raw_columns:
                e = c.bind
                if e:
                    self._bind = e
                    return e
        else:
            e = list(froms)[0].bind
            if e:
                self._bind = e
                return e

        return None

    def _set_bind(self, bind):
        self._bind = bind
    bind = property(bind, _set_bind)
