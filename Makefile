wasm:
	./opa.exe build -t wasm -e annex4ac/deny -o policy.wasm policies/annex4_validation.rego
	move /Y policy.wasm annex4ac\annex4_validation.wasm 