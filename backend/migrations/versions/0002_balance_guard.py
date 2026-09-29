"""Reject a materialized stock balance that differs from its movement ledger.

Revision ID: 0002_balance_guard
Revises: 0001_inventory
"""

from alembic import op

revision = "0002_balance_guard"
down_revision = "0001_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail visibly if an existing 0001 database has already diverged.
    op.execute("LOCK TABLE items, movements IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM items AS i
                LEFT JOIN movements AS m ON m.item_id = i.id
                GROUP BY i.id, i.current_stock
                HAVING i.current_stock IS DISTINCT FROM COALESCE(SUM(m.delta), 0)
            ) THEN
                RAISE EXCEPTION 'existing item stock differs from movement ledger';
            END IF;
        END;
        $$;
        """
    )
    # Deferred because the service inserts the movement and updates the balance
    # in one transaction. Direct SQL changing only one side fails at commit.
    op.execute(
        """
        CREATE FUNCTION verify_item_stock_ledger() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            target_item_id bigint;
            stored_stock numeric;
            ledger_stock numeric;
        BEGIN
            IF TG_TABLE_NAME = 'items' THEN
                target_item_id := NEW.id;
            ELSE
                target_item_id := NEW.item_id;
            END IF;
            SELECT current_stock INTO stored_stock FROM items WHERE id = target_item_id;
            SELECT COALESCE(SUM(delta), 0) INTO ledger_stock
              FROM movements WHERE item_id = target_item_id;
            IF stored_stock IS DISTINCT FROM ledger_stock THEN
                RAISE EXCEPTION 'item stock differs from movement ledger'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER items_stock_matches_ledger "
        "AFTER INSERT OR UPDATE ON items DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION verify_item_stock_ledger()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER movements_stock_matches_ledger "
        "AFTER INSERT ON movements DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION verify_item_stock_ledger()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER movements_stock_matches_ledger ON movements")
    op.execute("DROP TRIGGER items_stock_matches_ledger ON items")
    op.execute("DROP FUNCTION verify_item_stock_ledger()")
