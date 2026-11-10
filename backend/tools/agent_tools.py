"""
Agent tool definitions in OpenAI-compatible function-calling format
(used by Cloudflare Workers AI).
"""

AGENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Search for products, flights, hotels, or services that match "
                "the user's mandate. Returns a list of matching items with "
                "prices, descriptions, and availability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query describing what to find",
                    },
                    "category": {
                        "type": "string",
                        "description": "Product category (e.g. electronics, flights, food)",
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Maximum price in the mandate's currency",
                    },
                    "currency": {
                        "type": "string",
                        "description": "ISO 4217 currency code (default USD)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_mandate_policy",
            "description": (
                "Verify whether a proposed purchase complies with the mandate's "
                "spending constraints. Must be called BEFORE creating an order. "
                "A rejection cancels the mandate immediately; no retries are allowed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "Name of the product or service to purchase",
                    },
                    "price": {
                        "type": "number",
                        "description": "Price of the item as a JSON number, never a quoted string",
                    },
                    "currency": {
                        "type": "string",
                        "description": "ISO 4217 currency code",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category of the product or service",
                    },
                    "justification": {
                        "type": "string",
                        "description": "Brief explanation of why this purchase fits the mandate",
                    },
                },
                "required": ["product_name", "price", "currency", "category", "justification"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_authorized_order",
            "description": (
                "Create a PayPal order with AUTHORIZE intent using the mandate's "
                "vaulted payment token. The funds are held but NOT captured yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "Total amount to authorize",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category of the actual purchase for mandatory policy validation",
                    },
                    "currency": {
                        "type": "string",
                        "description": "ISO 4217 currency code (default USD)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description for the PayPal order",
                    },
                    "product_name": {
                        "type": "string",
                        "description": "Name of the product being purchased",
                    },
                },
                "required": ["amount", "currency", "description", "category", "product_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_authorized_payment",
            "description": (
                "Capture (finalize) an authorized PayPal payment. Only call this "
                "after the mandate policy check has APPROVED the purchase."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "authorization_id": {
                        "type": "string",
                        "description": "PayPal authorization ID to capture",
                    },
                },
                "required": ["authorization_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "void_authorization",
            "description": (
                "Void (cancel) an authorized PayPal payment, releasing the hold "
                "on the buyer's funds. Use when the purchase is rejected by policy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "authorization_id": {
                        "type": "string",
                        "description": "PayPal authorization ID to void",
                    },
                },
                "required": ["authorization_id"],
            },
        },
    },
]
