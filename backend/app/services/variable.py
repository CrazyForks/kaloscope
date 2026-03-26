from tortoise.expressions import Q

from app.core.exceptions import ErrorCode, KaloscopeException
from app.models.general import GlobalVariable, VariableUpsert
from app.services.base import BaseService
from app.utils.crypto import xor_encrypt


class VariableService(BaseService[GlobalVariable], model=GlobalVariable):
    """The service class for all global variable related operations."""

    @classmethod
    async def upsert(cls, obj: VariableUpsert) -> GlobalVariable:
        """Create or update a global variable.

        Args:
            obj: The global variable data.

        Raises:
            KaloscopeException: If the key already exists.

        Returns:
            The global variable instance.
        """
        # check if the key already exists
        filter = ~Q(id=obj.id) if obj.id else Q()
        if await GlobalVariable.filter(filter & Q(key=obj.key)).count() > 0:
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)

        if obj.id:
            # update the global variable
            await GlobalVariable.filter(id=obj.id).update(
                value=xor_encrypt(obj.value) if obj.encrypted else obj.value,
                value_length=len(obj.value),
            )
            variable = await GlobalVariable.get(id=obj.id)
        else:
            # create the global variable
            variable = await GlobalVariable.create(
                key=obj.key,
                value=xor_encrypt(obj.value) if obj.encrypted else obj.value,
                value_length=len(obj.value),
                encrypted=obj.encrypted,
            )

        return variable
