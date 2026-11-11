192 | REST Operation | publish OpenAPI specification for APIs
106 | REST Design - Compatibility | not break backward compatibility
108 | REST Design - Compatibility | prepare clients to accept compatible API extensions
111 | REST Design - Compatibility | treat OpenAPI specification as open for extension by default
114 | REST Design - Compatibility | use media type versioning
115 | REST Design - Compatibility | not use URL versioning
110 | REST Design - Compatibility | always return JSON objects as top-level data structures
238 | REST Basics - Data formats | use standard data formats
171 | REST Basics - Data formats | define a format for number and integer types
239 | REST Basics - Data formats | encode binary data in `base64url`
169 | REST Basics - Data formats | use standard formats for date and time properties
170 | REST Basics - Data formats | use standard formats for country, language and currency properties
187 | REST Design - Deprecation | reflect deprecation in API specifications
185 | REST Design - Deprecation | obtain approval of clients before API shut down
186 | REST Design - Deprecation | collect external partner consent on deprecation time span
188 | REST Design - Deprecation | monitor usage of deprecated API scheduled for sunset
191 | REST Design - Deprecation | not start using deprecated APIs
208 | EVENT Basics - Event Types | define events compliant with overall API guidelines
194 | EVENT Basics - Event Types | treat events as part of the service interface
195 | EVENT Basics - Event Types | make event schema available for review
197 | EVENT Basics - Event Types | specify and register events as event types
213 | EVENT Basics - Event Types | follow naming convention for event type names
207 | EVENT Basics - Event Types | indicate ownership of event types
245 | EVENT Basics - Event Types | carefully define the compatibility mode
196 | EVENT Basics - Event Types | ensure event schema conforms to OpenAPI schema object
246 | EVENT Basics - Event Types | use semantic versioning of event type schemas
198 | EVENT Basics - Event Types | ensure that events conform to an event category
247 | EVENT Basics - Event Types | provide mandatory event metadata
211 | EVENT Basics - Event Types | provide unique event identifiers
201 | EVENT Basics - Event Types | use general events to signal steps in business processes
202 | EVENT Basics - Event Types | use data change events to signal mutations
242 | EVENT Basics - Event Types | provide explicit event ordering for data change events
214 | EVENT Basics - Event Types | be robust against duplicates when consuming events
199 | EVENT Basics - Event Types | ensure that events define useful business resources
209 | EVENT Basics - Event Types | maintain backwards compatibility for events
100 | General guidelines | follow API first principle
101 | General guidelines | provide API Specifications using OpenAPI
103 | General guidelines | write APIs using U.S. English
234 | General guidelines | only use durable and immutable remote references
178 | REST Basics - HTTP headers | use `Content-*` headers correctly
184 | REST Basics - HTTP headers | propagate proprietary headers
233 | REST Basics - HTTP headers | support {X-Flow-ID}
148 | REST Basics - HTTP requests | use HTTP methods correctly
149 | REST Basics - HTTP requests | fulfill common method properties
154 | REST Basics - HTTP requests | define collection format of header and query parameters
226 | REST Basics - HTTP requests | document implicit response filtering
243 | REST Basics - HTTP status codes | use official HTTP status codes
151 | REST Basics - HTTP status codes | specify success and error responses
256 | REST Basics - HTTP status codes | preserve end-to-end semantics of HTTP status codes
220 | REST Basics - HTTP status codes | use most specific HTTP status codes
152 | REST Basics - HTTP status codes | use code 207 for batch or bulk requests
153 | REST Basics - HTTP status codes | use code 429 with headers for rate limits
176 | REST Basics - HTTP status codes | support problem JSON
177 | REST Basics - HTTP status codes | not expose stack traces
162 | REST Design - Hypermedia | use REST maturity level 2
164 | REST Design - Hypermedia | use common hypertext controls
217 | REST Design - Hypermedia | use full, absolute URI for resource identification (with same host, if applicable)
166 | REST Design - Hypermedia | not use link headers with JSON entities
167 | REST Basics - JSON payload | use JSON as payload data interchange format
118 | REST Basics - JSON payload | property names must be snake_case (and never camelCase)
123 | REST Basics - JSON payload | use same semantics for `null` and absent properties
122 | REST Basics - JSON payload | not use `null` for boolean properties
174 | REST Basics - JSON payload | use common field names and semantics
249 | REST Basics - JSON payload | use the common address fields
173 | REST Basics - JSON payload | use the common money object
218 | REST Basics - Meta information | contain API meta information
116 | REST Basics - Meta information | use semantic versioning
215 | REST Basics - Meta information | provide API identifiers
219 | REST Basics - Meta information | provide API audience
224 | REST Basics - Meta information | follow naming convention for hostnames
159 | REST Design - Pagination | support pagination
227 | REST Design - Performance | document cacheable `GET`, `HEAD`, and `POST` endpoints
104 | REST Basics - Security | secure endpoints
105 | REST Basics - Security | define and assign permissions (scopes)
225 | REST Basics - Security | follow the naming convention for permissions (scopes)
134 | REST Basics - URLs | pluralize resource names
228 | REST Basics - URLs | use URL-friendly resource identifiers
129 | REST Basics - URLs | use kebab-case for path segments
136 | REST Basics - URLs | use normalized paths without empty path segments and trailing slashes
141 | REST Basics - URLs | keep URLs verb-free
138 | REST Basics - URLs | avoid actions — think about resources
142 | REST Basics - URLs | use domain-specific resource names
143 | REST Basics - URLs | identify resources and sub-resources via path segments
130 | REST Basics - URLs | use snake_case (never camelCase) for query parameters
137 | REST Basics - URLs | stick to conventional query parameters
