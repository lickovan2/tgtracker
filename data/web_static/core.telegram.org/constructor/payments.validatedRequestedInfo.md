# payments.ValidatedRequestedInfo

Validated user-provided info

```tl
payments.validatedRequestedInfo#d1451883 flags:# id:flags.0?string shipping_options:flags.1?Vector<ShippingOption> = payments.ValidatedRequestedInfo;
```

### Parameters

| Name | Type | Description |
| --- | :---: | --- |
| **flags** | [\#](/type/%23) | Flags, see [TL conditional fields](/mtproto/TL-combinators.md#conditional-fields) |
| **id** | [flags](/mtproto/TL-combinators.md#conditional-fields).0?[string](/type/string.md) | ID |
| **shipping\_options** | [flags](/mtproto/TL-combinators.md#conditional-fields).1?[Vector](/type/Vector%20t)&lt;[ShippingOption](/type/ShippingOption.md)&gt; | Shipping options |

### Type
[payments.ValidatedRequestedInfo](/type/payments.ValidatedRequestedInfo.md)