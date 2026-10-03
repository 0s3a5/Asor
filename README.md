# Asor
En este github se presenta el codigo con el cual se realizo una busqueda en el diario oficial.

La explicacion del codigo es la siguiente:

1) se ingresa al sitio oficial del diario oficial
2) Recorre las fechas considerando tambien la edicion y saltandose los dias feriados
3) Verifica que exista la coincidencia entre fecha y edicion, si no hay edicion se salta la fecha
4) Clasifica lo encontrado, esto lo hace si dice decreto de la agencia nacional de ciberseguridad o la otra clasificacion es relacionado a la ciberseguridad
5) Deja constancia en otro csv, aqui se expresa la revision, fecha, html del Diario y pdf de este mismo
