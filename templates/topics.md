## Topics

Which if any of the following high-level topics best describe the news segment?

{% for topic in topics %}
* **{{topic.title}}**<br><small>{{topic.description}}</small><br>
{% endfor %}
