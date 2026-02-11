from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.core.exceptions import ErrorCode, KaloscopeException
from app.models.general import GlobalVariable, VariableUpsert
from app.services.base import BaseService
from app.utils.crypto import xor_encrypt


class VariableService(BaseService[GlobalVariable], model=GlobalVariable):
    """The service class for all global variable related operations."""

    @classmethod
    @atomic()
    async def upsert(cls, var: VariableUpsert) -> GlobalVariable:
        """Create or update a global variable.

        Args:
            var: The global variable data.

        Raises:
            KaloscopeException: If the key already exists.

        Returns:
            The global variable instance.
        """
        # check if the key already exists
        filter = ~Q(id=var.id) if var.id else Q()
        if await GlobalVariable.filter(filter & Q(key=var.key)).count() > 0:
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)

        if var.id:
            # update the global variable
            await GlobalVariable.filter(id=var.id).update(
                value=xor_encrypt(var.value) if var.encrypted else var.value,
                value_length=len(var.value),
            )
            variable = await GlobalVariable.get(id=var.id)
        else:
            # create the global variable
            variable = await GlobalVariable.create(
                key=var.key,
                value=xor_encrypt(var.value) if var.encrypted else var.value,
                value_length=len(var.value),
                encrypted=var.encrypted,
            )

        return variable
